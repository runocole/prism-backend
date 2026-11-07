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
                names |= {n for n in os.listdir(folder) if n.startswith("chunk_") and n.endswith(".webm")}
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


# ── Whole recording as one file (local disk) ─────────────────────────────────

def full_recording_path(session_id: int) -> str:
    return os.path.join(str(settings.PRIVATE_MEDIA_ROOT), "recordings", str(session_id), "full.webm")


def assemble_recording(session_id: int) -> str | None:
    """
    Join the 30-second pieces into recordings/<id>/full.webm (once, or again
    if new pieces arrived). If ffmpeg is installed it also fixes the video's
    duration so the seek bar works; without it the joined file still plays.
    Returns the file path, or None if there's nothing recorded.
    """
    if settings.STORAGE_ENDPOINT_URL:
        return None
    keys = recording_chunk_keys(session_id)
    if not keys:
        return None
    out = full_recording_path(session_id)
    newest = max(os.path.getmtime(_local_path(k)) for k in keys)
    if os.path.exists(out) and os.path.getmtime(out) >= newest:
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    parts = recording_parts(session_id)

    # Several parts (the page was refreshed): join each part, then stitch with ffmpeg
    if len(parts) > 1 and ffmpeg:
        import subprocess
        part_files = []
        for i, (_, part_keys) in enumerate(parts):
            pf = f"{out}.part{i}.webm"
            with open(pf, "wb") as dst:
                for k in part_keys:
                    with open(_local_path(k), "rb") as src:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
            part_files.append(pf)
        listing = out + ".list.txt"
        with open(listing, "w") as f:
            f.writelines(f"file '{pf}'\n" for pf in part_files)
        fixed = out + ".fixed.webm"
        result = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", fixed],
            capture_output=True, timeout=900,
        )
        for pf in part_files + [listing]:
            if os.path.exists(pf):
                os.remove(pf)
        if result.returncode == 0 and os.path.exists(fixed) and os.path.getsize(fixed) > 0:
            os.replace(fixed, out)
            return out
        if os.path.exists(fixed):
            os.remove(fixed)

    joined = out + ".joined"
    with open(joined, "wb") as dst:
        for k in keys:
            with open(_local_path(k), "rb") as src:
                shutil.copyfileobj(src, dst, 1024 * 1024)
    if ffmpeg:
        import subprocess
        fixed = out + ".fixed.webm"
        result = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-fflags", "+genpts", "-i", joined, "-c", "copy", fixed],
            capture_output=True, timeout=600,
        )
        if result.returncode == 0 and os.path.getsize(fixed) > 0:
            os.replace(fixed, out)
            os.remove(joined)
            return out
        if os.path.exists(fixed):
            os.remove(fixed)
    os.replace(joined, out)
    return out



# ── Recording parts ──────────────────────────────────────────────────────────
# Chunk names: chunk_<part>_<index>.webm, where <part> is the moment that page
# load started recording (13-digit ms). A page refresh starts a new part, so
# earlier video is never overwritten. Old names chunk_<index>.webm = part 0.

def _part_of(key: str) -> int:
    name = key.rsplit("/", 1)[-1][len("chunk_"):-len(".webm")]
    return int(name.split("_")[0]) if "_" in name else 0


def recording_parts(session_id: int) -> list[tuple[int, list[str]]]:
    """[(part, [chunk keys in order]), ...] in recording order."""
    parts: dict[int, list[str]] = {}
    for key in recording_chunk_keys(session_id):
        parts.setdefault(_part_of(key), []).append(key)
    return [(p, sorted(keys)) for p, keys in sorted(parts.items())]


def part_start_seconds(session_id: int) -> dict[int, float]:
    """Where each part starts in the joined recording (each chunk is ~30 s)."""
    starts, t = {}, 0.0
    for part, keys in recording_parts(session_id):
        starts[part] = t
        t += 30.0 * len(keys)
    return starts
