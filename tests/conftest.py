"""Shared pytest fixtures for the OTT poster automation test suite."""
from __future__ import annotations

import base64
import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

# Make project root importable.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Image fixtures
# ---------------------------------------------------------------------------


def _png_bytes(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def rgb_portrait_bytes() -> bytes:
    """A 1080x1920 RGB portrait PNG with a distinguishable pattern."""
    img = Image.new("RGB", (1080, 1920), color=(120, 60, 200))
    # Stripe pattern so resize is not a trivial identity.
    for x in range(0, 1080, 60):
        for y in range(1920):
            img.putpixel((x, y), (255, 255, 0))
    return _png_bytes(img)


@pytest.fixture
def rgb_landscape_bytes() -> bytes:
    """A 1920x1080 RGB landscape PNG."""
    img = Image.new("RGB", (1920, 1080), color=(30, 90, 150))
    return _png_bytes(img)


@pytest.fixture
def rgba_logo_bytes() -> bytes:
    """A 600x200 RGBA logo with a soft-edged red rectangle."""
    img = Image.new("RGBA", (600, 200), (0, 0, 0, 0))
    for x in range(80, 520):
        for y in range(40, 160):
            edge_dist = min(x - 80, 520 - x, y - 40, 160 - y)
            alpha = min(255, edge_dist * 32)
            img.putpixel((x, y), (220, 30, 30, alpha))
    return _png_bytes(img)


@pytest.fixture
def magenta_keyed_bytes() -> bytes:
    """A 100x100 image: solid magenta background with a small black square."""
    img = Image.new("RGB", (100, 100), color=(255, 0, 255))
    for x in range(40, 60):
        for y in range(40, 60):
            img.putpixel((x, y), (0, 0, 0))
    return _png_bytes(img)


@pytest.fixture
def small_png_bytes() -> bytes:
    """A tiny 8x8 RGB PNG suitable for use as a mock 'generated' image."""
    img = Image.new("RGB", (8, 8), color=(10, 20, 30))
    return _png_bytes(img)


@pytest.fixture
def small_png_data_url(small_png_bytes) -> str:
    """Same as small_png_bytes but encoded as a data: URL (OpenRouter format)."""
    b64 = base64.b64encode(small_png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


# ---------------------------------------------------------------------------
# Env / config fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    """Provide a fake OPENROUTER_API_KEY for every test (modules raise without one)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-api-key-deadbeef")


@pytest.fixture
def fake_openrouter_image_response(small_png_data_url):
    """OpenRouter-shaped JSON dict containing one generated image."""
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "images": [
                        {"image_url": {"url": small_png_data_url}}
                    ],
                }
            }
        ]
    }


@pytest.fixture
def fake_openrouter_text_response():
    """Build an OpenRouter chat-completions JSON with the given text content."""

    def _build(content: str) -> dict:
        return {
            "choices": [
                {"message": {"role": "assistant", "content": content}}
            ]
        }

    return _build
