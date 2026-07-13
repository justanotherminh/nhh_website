"""Shared Jinja2 templates instance, plus cache-busted static URLs.

Static files are served from a fixed path, so a browser will happily reuse its
cached copy of styles.css/seatmap.js after a deploy — old CSS against new markup
renders as a broken page. ``static()`` appends a short content hash, so changing a
file changes its URL and every visitor fetches the new one.
"""
import hashlib
from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent))

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_hashes: dict[str, str] = {}


def static_url(path: str) -> str:
    """``/static/<path>?v=<content-hash>`` — stable per build, new on every change."""
    v = _hashes.get(path)
    if v is None:
        try:
            v = hashlib.md5(
                (_STATIC_DIR / path).read_bytes(), usedforsecurity=False
            ).hexdigest()[:8]
        except OSError:
            v = "0"          # missing file: don't blow up rendering
        _hashes[path] = v
    return f"/static/{path}?v={v}"


templates.env.globals["static"] = static_url
