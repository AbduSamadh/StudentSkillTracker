"""Object storage: S3-compatible (UAE region in production) or local disk for development.
Downloads are always through short-lived signed URLs."""

import asyncio
import uuid
from pathlib import Path

from app.config import get_settings
from app.security import create_scoped_token


def _s3():  # noqa: ANN202
    import boto3

    s = get_settings()
    return boto3.client(
        "s3", endpoint_url=s.s3_endpoint_url, region_name=s.s3_region,
        aws_access_key_id=s.s3_access_key, aws_secret_access_key=s.s3_secret_key,
    )


def _local_path(key: str) -> Path:
    base = Path(get_settings().storage_local_path).resolve()
    p = (base / key).resolve()
    if base not in p.parents:
        raise ValueError("invalid storage key")
    return p


async def put(key: str, data: bytes, content_type: str) -> None:
    s = get_settings()
    if s.storage_backend == "s3":
        await asyncio.to_thread(
            _s3().put_object, Bucket=s.s3_bucket, Key=key, Body=data, ContentType=content_type,
            ServerSideEncryption="AES256",
        )
        return
    p = _local_path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(p.write_bytes, data)


async def get(key: str) -> bytes:
    s = get_settings()
    if s.storage_backend == "s3":
        obj = await asyncio.to_thread(_s3().get_object, Bucket=s.s3_bucket, Key=key)
        return obj["Body"].read()
    return await asyncio.to_thread(_local_path(key).read_bytes)


async def delete(key: str) -> None:
    s = get_settings()
    if s.storage_backend == "s3":
        await asyncio.to_thread(_s3().delete_object, Bucket=s.s3_bucket, Key=key)
        return
    p = _local_path(key)
    if p.exists():
        p.unlink()


async def healthy() -> bool:
    s = get_settings()
    try:
        if s.storage_backend == "s3":
            await asyncio.to_thread(_s3().head_bucket, Bucket=s.s3_bucket)
        else:
            def _probe() -> None:
                p = Path(s.storage_local_path)
                p.mkdir(parents=True, exist_ok=True)
                probe = p / f".health-{uuid.uuid4().hex}"
                probe.write_bytes(b"ok")
                probe.unlink()

            await asyncio.to_thread(_probe)
        return True
    except Exception:  # noqa: BLE001
        return False


def signed_url(tenant_id: uuid.UUID, key: str, filename: str, content_type: str) -> str:
    s = get_settings()
    if s.storage_backend == "s3":
        return _s3().generate_presigned_url(
            "get_object",
            Params={"Bucket": s.s3_bucket, "Key": key, "ResponseContentDisposition": f'attachment; filename="{filename}"'},
            ExpiresIn=s.signed_url_seconds,
        )
    token = create_scoped_token(
        "file", {"tid": str(tenant_id), "key": key, "fn": filename, "ct": content_type}, max(1, s.signed_url_seconds // 60)
    )
    return f"{s.api_base_url}/api/v1/files/{token}"
