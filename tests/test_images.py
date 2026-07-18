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


@pytest.fixture(autouse=True)
def _clean_uploads():
    """Remove any test files this module creates (keep the dir)."""
    created = set(p.name for p in img.UPLOAD_DIR.iterdir()) if img.UPLOAD_DIR.is_dir() else set()
    yield
    if img.UPLOAD_DIR.is_dir():
        for p in list(img.UPLOAD_DIR.iterdir()):
            if p.name not in created:
                p.unlink()


def test_save_and_list_and_delete():
    name = img.save_upload("My Photo!.png", _png_bytes())
    assert name.endswith(".png") and " " not in name and "!" not in name
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
    # it now appears in the listing and is served at /static/uploads/
    page = c.get("/admin/images", auth=admin_creds).text
    assert "/static/uploads/banner.png" in page
    served = c.get("/static/uploads/banner.png")
    assert served.status_code == 200 and served.headers["content-type"].startswith("image/")


def test_images_page_requires_auth():
    assert TestClient(app).get("/admin/images").status_code == 401
