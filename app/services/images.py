"""Manager-uploaded image library.

Files live in ``app/static/uploads`` — a persistent Docker volume on the server, so
uploads survive rebuilds and are never committed to git. They're served by the
existing StaticFiles mount at ``/static/uploads/<name>``.

Images the manager marks "show on homepage" are moved into the ``reel/`` subfolder,
which is what the hero reel reads. Membership is the file's location, so there's no
DB state to drift out of sync with the disk.

Uploads are re-encoded on the way in (downscaled + compressed) so a phone photo
straight off a camera can't bloat the homepage.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "static" / "uploads"
REEL_DIR = UPLOAD_DIR / "reel"
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_BYTES = 12 * 1024 * 1024  # 12 MB per upload
MAX_DIM = 2000                # longest edge after optimisation
JPEG_QUALITY = 82

Image.MAX_IMAGE_PIXELS = 80_000_000  # reject absurd decompression-bomb dimensions


class ImageError(Exception):
    """A human-readable reason an upload was rejected."""


def _safe_stem(name: str) -> str:
    stem = Path(name).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return stem or "image"


def _unique_path(stem: str, ext: str) -> Path:
    """A path in the library that collides with nothing in either folder."""
    i = 0
    while True:
        name = f"{stem}{ext}" if i == 0 else f"{stem}-{i}{ext}"
        if not (UPLOAD_DIR / name).exists() and not (REEL_DIR / name).exists():
            return UPLOAD_DIR / name
        i += 1


def _optimize(data: bytes) -> tuple[bytes, str]:
    """Downscale and recompress an image for web use. Returns (bytes, extension).

    Transparent images stay PNG; everything else becomes progressive JPEG. Animated
    GIFs are passed through untouched so they keep their animation.
    """
    im = Image.open(io.BytesIO(data))
    if (im.format or "").upper() == "GIF" and getattr(im, "is_animated", False):
        return data, ".gif"
    im.load()
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    if max(im.size) > MAX_DIM:
        im.thumbnail((MAX_DIM, MAX_DIM), Image.LANCZOS)
    buf = io.BytesIO()
    if has_alpha:
        im.convert("RGBA").save(buf, format="PNG", optimize=True)
        return buf.getvalue(), ".png"
    im.convert("RGB").save(
        buf, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True
    )
    return buf.getvalue(), ".jpg"


def save_upload(filename: str, data: bytes) -> str:
    """Validate, optimise and store one uploaded image. Returns the stored name."""
    ext = Path(filename or "").suffix.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in ALLOWED_EXT:
        raise ImageError(f"Định dạng không hỗ trợ: {ext or '?'} (chỉ JPG, PNG, WEBP, GIF).")
    if not data:
        raise ImageError("Tệp rỗng.")
    if len(data) > MAX_BYTES:
        raise ImageError(f"Tệp quá lớn ({len(data) // (1024*1024)} MB, tối đa 12 MB).")
    try:
        Image.open(io.BytesIO(data)).verify()   # is it really a decodable image?
        out, ext = _optimize(data)
    except ImageError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ImageError("Tệp không phải ảnh hợp lệ.") from exc

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = _unique_path(_safe_stem(filename), ext)
    path.write_bytes(out)
    return path.name


def _find(name: str) -> Path | None:
    """Resolve a user-supplied name to a real file in either folder, or None.

    Rejects path traversal / absolute paths by comparing resolved parents.
    """
    if not name or "/" in name or "\\" in name:
        return None
    for d in (UPLOAD_DIR, REEL_DIR):
        p = (d / name).resolve()
        if p.parent == d.resolve() and p.is_file():
            return p
    return None


def delete_image(name: str) -> bool:
    path = _find(name)
    if path is None:
        return False
    path.unlink()
    return True


def set_reel(name: str, on: bool) -> bool:
    """Show/hide an image on the homepage reel by moving it between folders."""
    path = _find(name)
    if path is None:
        return False
    dest_dir = REEL_DIR if on else UPLOAD_DIR
    if path.parent.resolve() == dest_dir.resolve():
        return True  # already where it should be
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem, ext = path.stem, path.suffix
    dest, i = dest_dir / path.name, 1
    while dest.exists():
        dest = dest_dir / f"{stem}-{i}{ext}"
        i += 1
    path.rename(dest)
    return True


def _entry(p: Path, in_reel: bool) -> dict:
    st = p.stat()
    dims = ""
    try:
        with Image.open(p) as im:
            dims = f"{im.width}×{im.height}"
    except Exception:  # noqa: BLE001
        pass
    rel = f"uploads/reel/{p.name}" if in_reel else f"uploads/{p.name}"
    return {
        "name": p.name,
        "url": f"/static/{rel}",
        "kb": max(1, st.st_size // 1024),
        "dims": dims,
        "mtime": int(st.st_mtime),
        "in_reel": in_reel,
    }


def list_images() -> list[dict]:
    """All images — homepage ones first, then the rest, newest first within each."""
    out: list[dict] = []
    for d, in_reel in ((REEL_DIR, True), (UPLOAD_DIR, False)):
        if not d.is_dir():
            continue
        files = [
            p for p in d.iterdir()
            if p.is_file() and p.suffix.lower() in ALLOWED_EXT
        ]
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        out.extend(_entry(p, in_reel) for p in files)
    return out


def reel_relpaths() -> list[str]:
    """Static-relative paths of the homepage reel images, in a stable order."""
    if not REEL_DIR.is_dir():
        return []
    files = sorted(
        p for p in REEL_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_EXT
    )
    return [f"uploads/reel/{p.name}" for p in files]
