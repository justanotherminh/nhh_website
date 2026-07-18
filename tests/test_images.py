"""Manager image uploads: validation, safe storage, listing, delete, and serving."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.main import app
from app.services import images as img


def _png_bytes(w=20, h=12, color=(40, 110, 180)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def admin_creds(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "s3cret-test")
    return ("admin", "s3cret-test")


def _snapshot() -> set[str]:
    names = set()
    for d in (img.UPLOAD_DIR, img.REEL_DIR):
        if d.is_dir():
            names |= {p.name for p in d.iterdir() if p.is_file()}
    return names


@pytest.fixture(autouse=True)
def _clean_uploads():
    """Remove any test files this module creates (keep pre-existing ones)."""
    before = _snapshot()
    yield
    for d in (img.UPLOAD_DIR, img.REEL_DIR):
        if d.is_dir():
            for p in list(d.iterdir()):
                if p.is_file() and p.name not in before:
                    p.unlink()


def test_save_and_list_and_delete():
    name = img.save_upload("My Photo!.png", _png_bytes())
    # name is sanitised; the extension reflects the optimised encoding
    assert name.startswith("my-photo") and " " not in name and "!" not in name
    listed = [i["name"] for i in img.list_images()]
    assert name in listed
    assert img.delete_image(name) is True
    assert name not in [i["name"] for i in img.list_images()]


def test_rejects_non_image():
    with pytest.raises(img.ImageError):
        img.save_upload("evil.png", b"this is not a real image")


def test_rejects_bad_extension():
    with pytest.raises(img.ImageError):
        img.save_upload("script.svg", _png_bytes())


def test_delete_rejects_path_traversal():
    assert img.delete_image("../../etc/passwd") is False
    assert img.delete_image("/etc/passwd") is False


def test_unique_names_avoid_collision():
    a = img.save_upload("dup.png", _png_bytes())
    b = img.save_upload("dup.png", _png_bytes())
    assert a != b  # second one gets a -1 suffix


def test_admin_upload_route_and_serving(admin_creds):
    c = TestClient(app)
    r = c.post(
        "/admin/images", auth=admin_creds, follow_redirects=False,
        files={"files": ("banner.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 303 and "notice=" in r.headers["location"]
    # it now appears in the listing and is served from /static/uploads/
    entry = next(i for i in img.list_images() if i["name"].startswith("banner"))
    page = c.get("/admin/images", auth=admin_creds).text
    assert entry["url"] in page
    served = c.get(entry["url"])
    assert served.status_code == 200 and served.headers["content-type"].startswith("image/")


def test_images_page_requires_auth():
    assert TestClient(app).get("/admin/images").status_code == 401


def test_large_upload_is_downscaled():
    """A huge photo is resized/recompressed so it can't bloat the homepage."""
    big = _png_bytes(4000, 3000)
    name = img.save_upload("huge.png", big)
    entry = next(i for i in img.list_images() if i["name"] == name)
    w, h = entry["dims"].split("×")
    assert max(int(w), int(h)) == img.MAX_DIM      # longest edge capped
    assert name.endswith(".jpg")                    # opaque -> JPEG


def test_transparent_upload_stays_png():
    buf = io.BytesIO()
    Image.new("RGBA", (30, 20), (0, 0, 0, 0)).save(buf, format="PNG")
    name = img.save_upload("logo.png", buf.getvalue())
    assert name.endswith(".png")


def test_reel_toggle_moves_image_and_feeds_homepage():
    name = img.save_upload("moment.png", _png_bytes())
    assert img.reel_relpaths() == [] or all("moment" not in r for r in img.reel_relpaths())

    assert img.set_reel(name, True) is True
    rels = img.reel_relpaths()
    assert any(r.startswith("uploads/reel/") for r in rels)
    entry = next(i for i in img.list_images() if i["name"].startswith("moment"))
    assert entry["in_reel"] is True

    # the homepage now serves the uploaded reel image instead of placeholders
    html = TestClient(app).get("/").text
    assert "/static/uploads/reel/" in html

    # toggling off returns it to the library and off the homepage
    assert img.set_reel(entry["name"], False) is True
    assert all("moment" not in r for r in img.reel_relpaths())


def test_admin_reel_toggle_route(admin_creds):
    name = img.save_upload("banner2.png", _png_bytes())
    c = TestClient(app)
    r = c.post("/admin/images/reel", auth=admin_creds, follow_redirects=False,
               data={"name": name, "on": "1"})
    assert r.status_code == 303
    assert any(name.rsplit(".", 1)[0] in rel for rel in img.reel_relpaths())
