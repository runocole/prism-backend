"""
exam_sessions/storage.py
─────────────────────────
Where recording chunks and webcam snapshots are kept.

- If STORAGE_ENDPOINT_URL is set: Hostinger Object Storage (S3-compatible).
- Otherwise: the server's disk under PRIVATE_MEDIA_ROOT, which the web server
  does NOT serve publicly. Staff get recordings through an authenticated /
  signed API endpoint instead (see RecordingStreamView).

Older files written before this change live under MEDIA_ROOT; they're still
read from there as a fallback.
"""

import os
import shutil

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


def _local_roots():
    return [str(settings.PRIVATE_MEDIA_ROOT), str(settings.MEDIA_ROOT)]


def _local_path(object_key: str) -> str:
    """Existing file (private first, then legacy public), or the private path for new files."""
    for root in _local_roots():
        path = os.path.join(root, object_key)
        if os.path.exists(path):
            return path
    return os.path.join(str(settings.PRIVATE_MEDIA_ROOT), object_key)


def upload_file(file_obj, object_key: str, content_type: str = "application/octet-stream") -> str:
    """Store a file-like object. Returns the object key."""
    if not settings.STORAGE_ENDPOINT_URL:
        local_path = os.path.join(str(settings.PRIVATE_MEDIA_ROOT), object_key)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        content = file_obj.read() if hasattr(file_obj, "read") else file_obj
        with open(local_path, "wb") as f:
            f.write(content)
        return object_key

    _get_client().upload_fileobj(
        file_obj,
        settings.STORAGE_BUCKET_NAME,
        object_key,
        ExtraArgs={"ContentType": content_type},
    )
    return object_key


def generate_signed_url(object_key: str, expires_in: int = 3600) -> str:
    """Object storage only: a time-limited URL for one object."""
    return _get_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.STORAGE_BUCKET_NAME, "Key": object_key},
        ExpiresIn=expires_in,
    )


def read_file(object_key: str) -> bytes:
    if not settings.STORAGE_ENDPOINT_URL:
        with open(_local_path(object_key), "rb") as f:
            return f.read()
    obj = _get_client().get_object(Bucket=settings.STORAGE_BUCKET_NAME, Key=object_key)
    return obj["Body"].read()


def recording_chunk_keys(session_id: int) -> list[str]:
    """All of a session's 30-second recording pieces, in order."""
    prefix = f"recordings/{session_id}/"
    if not settings.STORAGE_ENDPOINT_URL:
        names = set()
        for root in _local_roots():
            folder = os.path.join(root, "recordings", str(session_id))
            if os.path.isdir(folder):
                names |= {n for n in os.listdir(folder) if n.endswith(".webm")}
        return [prefix + n for n in sorted(names)]
    keys, token = [], None
    client = _get_client()
    while True:
        kwargs = {"Bucket": settings.STORAGE_BUCKET_NAME, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        keys += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(".webm")]
        if not page.get("IsTruncated"):
            return sorted(keys)
        token = page["NextContinuationToken"]


def iter_recording(session_id: int, chunk_size: int = 256 * 1024):
    """
    The whole recording as one stream. The browser records one continuous
    WebM in 30-second pieces, so joining the pieces in order gives one
    playable video (previously only the first piece was played: 29 seconds).
    """
    for key in recording_chunk_keys(session_id):
        if not settings.STORAGE_ENDPOINT_URL:
            with open(_local_path(key), "rb") as f:
                while block := f.read(chunk_size):
                    yield block
        else:
            body = _get_client().get_object(Bucket=settings.STORAGE_BUCKET_NAME, Key=key)["Body"]
            while block := body.read(chunk_size):
                yield block


def delete_session_files(session_id: int):
    """Remove a session's recording chunks and snapshot."""
    if settings.STORAGE_ENDPOINT_URL:
        client = _get_client()
        for prefix in (f"recordings/{session_id}/", f"snapshots/{session_id}/"):
            page = client.list_objects_v2(Bucket=settings.STORAGE_BUCKET_NAME, Prefix=prefix)
            for o in page.get("Contents", []):
                client.delete_object(Bucket=settings.STORAGE_BUCKET_NAME, Key=o["Key"])
        return
    for root in _local_roots():
        for folder in ("recordings", "snapshots"):
            shutil.rmtree(os.path.join(root, folder, str(session_id)), ignore_errors=True)
