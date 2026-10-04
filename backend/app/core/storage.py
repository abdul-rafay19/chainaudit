"""Safe file storage: magic-byte validation, size limits, hash-based paths."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.core.errors import AppError

MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg"}
EXT_TO_KIND = {".pdf": "pdf", ".png": "png", ".jpg": "jpg", ".jpeg": "jpg"}
ALLOWED_HINT = "PDF, PNG, JPG/JPEG"


def sniff_kind(data: bytes) -> str | None:
    """Detect the real file type from magic bytes."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    return None


def sanitize_display_name(name: str | None) -> str:
    """For display only. Never used to build a path."""
    base = (name or "unnamed").replace("\\", "/").split("/")[-1]
    base = unicodedata.normalize("NFKC", base)
    base = re.sub(r"[\x00-\x1f\x7f<>\"|?*]", "", base).strip().strip(".")
    return (base or "unnamed")[:120]


@dataclass
class ValidatedUpload:
    display_name: str
    kind: str
    mime: str
    data: bytes
    sha256: str


async def read_limited(f: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await f.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise AppError(413, "FILE_TOO_LARGE", f"'{sanitize_display_name(f.filename)}' exceeds the {max_bytes // (1024 * 1024)} MB limit")
        chunks.append(chunk)
    return b"".join(chunks)


def validate_upload(filename: str | None, data: bytes) -> ValidatedUpload:
    display = sanitize_display_name(filename)
    if not data:
        raise AppError(400, "EMPTY_FILE", f"'{display}' is empty")
    kind = sniff_kind(data)
    if kind is None:
        raise AppError(415, "UNSUPPORTED_FILE_TYPE", f"'{display}' is not a supported file. Accepted: {ALLOWED_HINT}")
    ext = Path(display).suffix.lower()
    if EXT_TO_KIND.get(ext) != kind:
        raise AppError(415, "EXTENSION_MISMATCH", f"'{display}': file extension does not match its content ({kind})")
    return ValidatedUpload(display, kind, MIME[kind], data, hashlib.sha256(data).hexdigest())


def store_bytes(upload_dir: Path, workflow_id: str, v: ValidatedUpload) -> Path:
    """Path is derived only from workflow_id (server generated) + content hash."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", workflow_id):
        raise ValueError("invalid workflow id")
    folder = (upload_dir / workflow_id).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{v.sha256}.{v.kind}"
    if not path.exists():
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(v.data)
        tmp.replace(path)
    return path
