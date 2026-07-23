"""Internationalisation: the catalog, the language cookie/toggle, and that the
public pages, seat-map JSON and confirmation email all follow the chosen language.

Vietnamese is the default; English is opt-in via the ``lang`` cookie. These tests
pin the switch working end-to-end and, just as importantly, that the default path
is unchanged from before i18n existed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import i18n
from app.main import app
from app.services import tickets as ticket_svc

client = TestClient(app)


# ---- catalog ----------------------------------------------------------------

def test_every_string_has_both_languages():
    missing = [k for k, v in i18n.STRINGS.items() if "vi" not in v or "en" not in v]
    assert not missing, f"catalog entries missing a language: {missing}"


def test_t_falls_back_and_formats():
    assert i18n.t("nav.about", "en") == "About"
    assert i18n.t("nav.about", "vi") == "Về chương trình"
    # Unknown language -> Vietnamese default; unknown key -> the key itself.
    assert i18n.t("nav.about", "fr") == "Về chương trình"
    assert i18n.t("does.not.exist", "en") == "does.not.exist"
    # {n} placeholder substitution.
    assert i18n.t("err.too_many_seats", "en", n=8) == "You can hold at most 8 seats at a time."


def test_floor_and_seat_label():
    assert i18n.floor_name("Tầng 3", "en") == "Floor 3"
    assert i18n.floor_name("Tầng 3", "vi") == "Tầng 3"
    seat = SimpleNamespace(section="Tầng 1", row_label="A", seat_number=12,
                           label="Tầng 1 – Hàng A – Ghế 12")
    assert i18n.seat_label(seat, "en") == "Floor 1 – Row A – Seat 12"
    # Vietnamese reuses the stored label verbatim.
    assert i18n.seat_label(seat, "vi") == "Tầng 1 – Hàng A – Ghế 12"


# ---- language cookie / toggle route -----------------------------------------

def test_home_is_vietnamese_by_default():
    r = client.get("/")
    assert r.status_code == 200
    assert 'lang="vi"' in r.text
    assert "Về chương trình" in r.text
    assert "Đặt vé" in r.text


def test_home_switches_to_english_with_cookie():
    r = client.get("/", cookies={"lang": "en"})
    assert 'lang="en"' in r.text
    assert "About" in r.text
    assert "Book tickets" in r.text
    # A Vietnamese-only body string should be gone.
    assert "Về chương trình" not in r.text


def test_lang_route_sets_cookie_and_redirects_back():
    r = client.get("/lang/en?next=/tickets", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/tickets"
    assert r.cookies.get("lang") == "en"


def test_lang_route_rejects_open_redirect_and_bad_code():
    r = client.get("/lang/en?next=//evil.com", follow_redirects=False)
    assert r.headers["location"] == "/"
    # Unknown code falls back to the default rather than storing garbage.
    r2 = client.get("/lang/zz?next=/", follow_redirects=False)
    assert r2.cookies.get("lang") == "vi"


# ---- seat-map JSON ----------------------------------------------------------

def test_seatmap_labels_follow_language():
    vi = client.get("/api/seatmap").json()
    en = client.get("/api/seatmap", cookies={"lang": "en"}).json()
    assert vi["stage"]["label"] == "SÂN KHẤU"
    assert en["stage"]["label"] == "STAGE"
    en_floors = {r["floor"] for r in en["floorRegions"]}
    assert any(f.startswith("Floor ") for f in en_floors)
    assert all(not f.startswith("Tầng") for f in en_floors)


# ---- confirmation email -----------------------------------------------------

def test_email_language_follows_order():
    def order(lang):
        seat = SimpleNamespace(section="Tầng 1", row_label="A", seat_number=5,
                               label="Tầng 1 – Hàng A – Ghế 5", tier=SimpleNamespace(name="Sông Trời"))
        tk = SimpleNamespace(id=1, seat=seat, qr_token="tok")
        return SimpleNamespace(kind="sale", lang=lang, buyer_name="Jane", amount_vnd=1_500_000,
                               order_code=99, tickets=[tk])

    en_html = ticket_svc._email_html(order("en"), "en")
    vi_html = ticket_svc._email_html(order("vi"), "vi")
    assert "YOUR TICKET AND DONATION ARE CONFIRMED!" in en_html
    assert "Floor 1 – Row A – Seat 5" in en_html
    assert "BẠN ĐÃ ĐĂNG KÝ ĐẶT VÉ VÀ QUYÊN GÓP THÀNH CÔNG!" in vi_html
    assert i18n.t("email.subject", "en").startswith("[NẮNG HOÀNG HÔN 2026]")
