"""Giữ giao diện đúng Việt Anh Design System (refactor 2026-07, theo Brand Guideline v2.0):
navy #14153A + CTA vàng #F9DD0E, font Manrope (chỉ sans-serif), cỡ chữ tối thiểu 16px."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
HTML_PAGES = [ROOT / "app" / "static" / "admin.html", ROOT / "app" / "static" / "index.html"]
FRONTEND_SOURCES = [ROOT / "frontend" / "src" / "App.jsx", ROOT / "frontend" / "src" / "styles.css",
                    ROOT / "frontend" / "index.html", ROOT / "frontend" / "tailwind.config.js"]
SERIF_FONTS = re.compile(r"Georgia|Times New Roman|DM Serif|Courier|font-display", re.IGNORECASE)
OLD_PALETTE = re.compile(r"#(?:0D2B55|F07D00|123d52|126d6a|0c4d4b|f4c95d|e9795c|e9785b|f7f5ef)\b", re.IGNORECASE)


def _font_sizes_px(text: str) -> list[float]:
    return [float(value) for value in re.findall(r"font(?:-size)?:\s*(?:[0-9]+\s+)?([0-9.]+)px", text)]


@pytest.mark.parametrize("page", HTML_PAGES, ids=lambda path: path.name)
def test_static_pages_follow_brand_typography_and_colors(page):
    text = page.read_text(encoding="utf-8")

    assert "Manrope" in text
    assert "#14153A" in text
    assert not SERIF_FONTS.search(text)
    assert not OLD_PALETTE.search(text)
    too_small = [size for size in _font_sizes_px(text) if size < 16]
    assert not too_small, f"cỡ chữ nhỏ hơn 16px: {too_small}"


@pytest.mark.parametrize("source", FRONTEND_SOURCES, ids=lambda path: path.name)
def test_react_frontend_uses_brand_tokens_only(source):
    text = source.read_text(encoding="utf-8")

    assert not OLD_PALETTE.search(text)
    assert not re.search(r"font-family:[^;]*serif", text.replace("sans-serif", ""))
    if source.suffix == ".jsx":
        # text-xs (12px) / text-sm (14px) / text-[<16px] đều dưới mức tối thiểu 16px.
        assert not re.search(r"\btext-(?:xs|sm)\b|\btext-\[(?:[0-9]|1[0-5])(?:\.[0-9]+)?px\]", text)
        assert "font-display" not in text


def test_tailwind_theme_matches_brand_palette():
    config = (ROOT / "frontend" / "tailwind.config.js").read_text(encoding="utf-8")

    for hex_code in ("#14153A", "#F9DD0E", "#E8C40A", "#FAF9F4", "#1A1A2E", "#E7E5DA"):
        assert hex_code in config
    assert "Manrope" in config


def test_brand_assets_are_served_for_every_page():
    client = TestClient(app)  # không dùng "with": lifespan (VaultWatcher) không chạy

    logo = client.get("/brand/logo-vietanh.webp")
    favicon = client.get("/brand/favicon.png")

    assert logo.status_code == 200 and logo.headers["content-type"] == "image/webp"
    assert favicon.status_code == 200 and favicon.headers["content-type"] == "image/png"
    admin = client.get("/admin")
    assert "/brand/logo-vietanh.webp" in admin.text and "/brand/favicon.png" in admin.text
    # Trang HTML không được dùng bản cache cũ (trỏ tới file CSS/JS của bản build trước)
    assert admin.headers["cache-control"] == "no-cache"
    assert client.get("/").headers["cache-control"] == "no-cache"
