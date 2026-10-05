"""Field-encryption key rotation (docs/operations.md, "Rotating keys").

Re-encrypts every encrypted field in a tenant under the current key, and rebuilds the guardian
email blind index with the current blind-index key, so either key can be replaced:

1. Deploy with the new key as STEM_FIELD_ENCRYPTION_KEY and the old one in
   STEM_FIELD_ENCRYPTION_PREVIOUS_KEYS (and/or a new STEM_BLIND_INDEX_KEY).
2. Run ``python -m app.cli rotate-keys``.
3. Remove the old key from STEM_FIELD_ENCRYPTION_PREVIOUS_KEYS and redeploy.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Guardian, Tenant, User, WebhookSubscription
from app.security import blind_index, decrypt_field, rotate_field


async def rotate_tenant(session: AsyncSession, tenant: Tenant) -> dict[str, int]:
    counts = {"guardians": 0, "users": 0, "webhooks": 0, "tenant_secrets": 0}
    for g in (await session.scalars(select(Guardian))).all():
        g.email_enc = rotate_field(g.email_enc)
        g.phone_enc = rotate_field(g.phone_enc)
        g.whatsapp_enc = rotate_field(g.whatsapp_enc)
        g.email_hash = blind_index(decrypt_field(g.email_enc))
        counts["guardians"] += 1
    for u in (await session.scalars(select(User).where(User.mfa_secret_enc.is_not(None)))).all():
        u.mfa_secret_enc = rotate_field(u.mfa_secret_enc)
        counts["users"] += 1
    for w in (await session.scalars(select(WebhookSubscription))).all():
        w.secret_enc = rotate_field(w.secret_enc) or w.secret_enc
        counts["webhooks"] += 1
    oidc = dict((tenant.settings or {}).get("oidc") or {})
    if oidc.get("client_secret_enc"):
        oidc["client_secret_enc"] = rotate_field(oidc["client_secret_enc"])
        tenant.settings = {**tenant.settings, "oidc": oidc}
        counts["tenant_secrets"] += 1
    await session.flush()
    return counts
