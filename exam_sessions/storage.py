"""
exam_sessions/storage.py
─────────────────────────
Thin wrapper around boto3 for Hostinger Object Storage (S3-compatible).
All recording chunks and snapshots go through here.

In local development with no storage credentials configured,
files fall back to local MEDIA_ROOT so you can still test uploads.
"""

import os
import boto3
from django.conf import settings


def _get_client():
    """Return a configured S3 client pointing at Hostinger Object Storage."""
    return boto3.client(
        "s3",
        endpoint_url=settings.STORAGE_ENDPOINT_URL,
        aws_access_key_id=settings.STORAGE_ACCESS_KEY,
        aws_secret_access_key=settings.STORAGE_SECRET_KEY,
    )


def upload_file(file_obj, object_key: str, content_type: str = "application/octet-stream") -> str:
    """
    Upload a file-like object to object storage.
    Returns the object key on success.
    Falls back to local media storage if storage is not configured.
    """
    if not settings.STORAGE_ENDPOINT_URL:
        # Local dev fallback — save to MEDIA_ROOT
        local_path = os.path.join(settings.MEDIA_ROOT, object_key)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        content = file_obj.read() if hasattr(file_obj, "read") else file_obj
        with open(local_path, "wb") as f:
            f.write(content)
        return object_key

    client = _get_client()
    client.upload_fileobj(
        file_obj,
        settings.STORAGE_BUCKET_NAME,
        object_key,
        ExtraArgs={"ContentType": content_type},
    )
    return object_key


def generate_signed_url(object_key: str, expires_in: int = 3600) -> str:
    """
    Generate a time-limited signed URL for private access to a recording.
    Default expiry: 1 hour.
    Falls back to a plain local media URL in development.
    """
    if not settings.STORAGE_ENDPOINT_URL:
        return f"{settings.MEDIA_URL}{object_key}"

    client = _get_client()
    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.STORAGE_BUCKET_NAME,
            "Key": object_key,
        },
        ExpiresIn=expires_in,
    )

def read_file(object_key: str) -> bytes:
    """Read a stored file (local media in dev, object storage in production)."""
    if not settings.STORAGE_ENDPOINT_URL:
        with open(os.path.join(settings.MEDIA_ROOT, object_key), "rb") as f:
            return f.read()
    obj = _get_client().get_object(Bucket=settings.STORAGE_BUCKET_NAME, Key=object_key)
    return obj["Body"].read()


def delete_session_files(session_id: int):
    """Remove a session's recording chunks and snapshot (local media only)."""
    if settings.STORAGE_ENDPOINT_URL:
        return  # object storage: left to the bucket's own lifecycle rules
    import shutil
    for folder in ("recordings", "snapshots"):
        shutil.rmtree(os.path.join(settings.MEDIA_ROOT, folder, str(session_id)), ignore_errors=True)
