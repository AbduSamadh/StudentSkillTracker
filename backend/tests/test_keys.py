"""Field-encryption keys can be rotated without losing data (docs/operations.md, "Rotating keys")."""

from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from app.cli import provision_tenant
from app.config import get_settings
from app.db import tenant_session
from app.models import Guardian, Tenant, User, WebhookSubscription
from app.security import blind_index, decrypt_field, encrypt_field, rotate_field
from app.services.keys import rotate_tenant


@pytest.fixture
def keys() -> Iterator[None]:
    s = get_settings()
    saved = (s.field_encryption_key, s.field_encryption_previous_keys, s.blind_index_key)
    yield
    s.field_encryption_key, s.field_encryption_previous_keys, s.blind_index_key = saved


async def test_rotation_reencrypts_everything_under_the_new_key(keys: None) -> None:
    s = get_settings()
    tid = await provision_tenant("keys", "Key Rotation School")
    async with tenant_session(tid) as session:
        g = Guardian(
            full_name="Rania Saleh",
            email_enc=encrypt_field("Rania@Family.example"),
            email_hash=blind_index("Rania@Family.example"),
            phone_enc=encrypt_field("+971500000001"),
        )
        u = User(
            email="mfa@keys.test", display_name="MFA User", mfa_secret_enc=encrypt_field("JBSWY3DPEHPK3PXP")
        )
        session.add_all([g, u])
        await session.flush()
        session.add(
            WebhookSubscription(url="https://hooks.example/x", secret_enc=encrypt_field("hook-secret"))
        )
        tenant = await session.get(Tenant, tid)
        assert tenant is not None
        tenant.settings = {**tenant.settings, "oidc": {"client_secret_enc": encrypt_field("oidc-secret")}}
        await session.commit()

    old_key, new_key = s.field_encryption_key, Fernet.generate_key().decode()
    s.field_encryption_key, s.field_encryption_previous_keys = new_key, [old_key]
    s.blind_index_key = "rotated-blind-index-key"
    async with tenant_session(tid) as session:
        tenant = await session.get(Tenant, tid)
        assert tenant is not None
        counts = await rotate_tenant(session, tenant)
        await session.commit()
    assert counts == {"guardians": 1, "users": 1, "webhooks": 1, "tenant_secrets": 1}

    s.field_encryption_previous_keys = []  # the old key is retired: everything must read without it
    async with tenant_session(tid) as session:
        g2 = await session.scalar(
            select(Guardian).where(Guardian.email_hash == blind_index("rania@family.example"))
        )
        assert g2 is not None, "lookup by email must work with the new blind-index key"
        assert decrypt_field(g2.email_enc) == "Rania@Family.example"
        assert decrypt_field(g2.phone_enc) == "+971500000001"
        u2 = await session.scalar(select(User).where(User.email == "mfa@keys.test"))
        assert u2 is not None and decrypt_field(u2.mfa_secret_enc) == "JBSWY3DPEHPK3PXP"
        w2 = await session.scalar(select(WebhookSubscription))
        assert w2 is not None and decrypt_field(w2.secret_enc) == "hook-secret"
        tenant = await session.get(Tenant, tid)
        assert tenant is not None
        assert decrypt_field(tenant.settings["oidc"]["client_secret_enc"]) == "oidc-secret"


def test_rotation_refuses_values_no_configured_key_can_read(keys: None) -> None:
    s = get_settings()
    stranger = Fernet(Fernet.generate_key()).encrypt(b"unknown").decode()
    with pytest.raises(InvalidToken):
        rotate_field(stranger)
    s.field_encryption_previous_keys = [s.field_encryption_key]
    s.field_encryption_key = Fernet.generate_key().decode()
    assert decrypt_field(rotate_field(encrypt_field("x"))) == "x"
