"""Manager-uploaded image library.

Files live in ``app/static/uploads`` — a persistent Docker volume on the server, so
uploads survive rebuilds and are never committed to git. They're served by the
existing StaticFiles mount at ``/static/uploads/<name>``. This module handles safe
storage, validation and listing; the admin routes are the UI.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "static" / "uploads"
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_BYTES = 12 * 1024 * 1024  # 12 MB per file

Image.MAX_IMAGE_PIXELS = 80_000_000  # reject absurd decompression-bomb dimensions


class ImageError(Exception):
    """A human-readable reason an upload was rejected."""


def _safe_stem(name: str) -> str:
    stem = Path(name).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return stem or "image"


def _unique_path(stem: str, ext: str) -> Path:
    """A non-colliding path: <stem><ext>, then <stem>-1<ext>, -2, …"""
    candidate = UPLOAD_DIR / f"{stem}{ext}"
    i = 1
    while candidate.exists():
        candidate = UPLOAD_DIR / f"{stem}-{i}{ext}"
        i += 1
    return candidate


def save_upload(filename: str, data: bytes) -> str:
    """Validate and store one uploaded image. Returns the stored file name."""
    ext = Path(filename or "").suffix.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in ALLOWED_EXT:
        raise ImageError(f"Định dạng không hỗ trợ: {ext or '?'} (chỉ JPG, PNG, WEBP, GIF).")
    if not data:
        raise ImageError("Tệp rỗng.")
    if len(data) > MAX_BYTES:
        raise ImageError(f"Tệp quá lớn ({len(data) // (1024*1024)} MB, tối đa 12 MB).")
    # Confirm it's really a decodable image (not just a matching extension).
    try:
        Image.open(io.BytesIO(data)).verify()
    except Exception as exc:  # noqa: BLE001
        raise ImageError("Tệp không phải ảnh hợp lệ.") from exc

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = _unique_path(_safe_stem(filename), ext)
    path.write_bytes(data)
    return path.name


def _resolve_within(name: str) -> Path | None:
    """Resolve a user-supplied name to a real file inside UPLOAD_DIR, or None.

    Rejects path-traversal / absolute paths by comparing resolved parents.
    """
    if not name or "/" in name or "\\" in name:
        return None
    path = (UPLOAD_DIR / name).resolve()
    if path.parent != UPLOAD_DIR.resolve() or not path.is_file():
        return None
    return path


def delete_image(name: str) -> bool:
    path = _resolve_within(name)
    if path is None:
        return False
    path.unlink()
    return True


def list_images() -> list[dict]:
    """All uploaded images, newest first: name, url, size (KB), dimensions."""
    if not UPLOAD_DIR.is_dir():
        return []
    out = []
    for p in sorted(UPLOAD_DIR.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
        if p.suffix.lower() not in ALLOWED_EXT or not p.is_file():
            continue
        st = p.stat()
        dims = ""
        try:
            with Image.open(p) as im:
                dims = f"{im.width}×{im.height}"
        except Exception:  # noqa: BLE001
            pass
        out.append({
            "name": p.name,
            "url": f"/static/uploads/{p.name}",
            "kb": max(1, st.st_size // 1024),
            "dims": dims,
            "mtime": int(st.st_mtime),
        })
    return out
