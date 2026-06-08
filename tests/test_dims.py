"""Tests for dims.py — PIL exact-pixel snapping + alpha utilities."""
from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

import dims


# ---------------------------------------------------------------------------
# snap() — exact dimensions per spec
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec_key,expected",
    [
        ("portrait", (900, 1600)),
        ("landscape", (1600, 900)),
        ("title", (580, 200)),
        ("banner", (1520, 536)),
    ],
)
def test_snap_exact_dimensions_per_spec(rgb_portrait_bytes, spec_key, expected):
    out = dims.snap(rgb_portrait_bytes, dims.SPECS[spec_key], mode="crop")
    with Image.open(BytesIO(out)) as img:
        assert img.size == expected, f"{spec_key}: expected {expected}, got {img.size}"


def test_snap_landscape_from_landscape_source(rgb_landscape_bytes):
    out = dims.snap(rgb_landscape_bytes, dims.SPECS["landscape"], mode="crop")
    with Image.open(BytesIO(out)) as img:
        assert img.size == (1600, 900)


def test_snap_preserves_rgba(rgba_logo_bytes):
    out = dims.snap(rgba_logo_bytes, (580, 200), mode="resize")
    with Image.open(BytesIO(out)) as img:
        assert img.mode == "RGBA"
        assert img.size == (580, 200)


def test_snap_crop_mode_preserves_target_aspect(rgb_portrait_bytes):
    # Portrait source (1080x1920, ratio 0.5625) snapped to landscape (1600x900,
    # ratio ~1.778) must center-crop to the target aspect, not stretch.
    out = dims.snap(rgb_portrait_bytes, (1600, 900), mode="crop")
    with Image.open(BytesIO(out)) as img:
        w, h = img.size
        assert (w, h) == (1600, 900)
        # Aspect must equal target (1600/900) within float precision.
        assert abs((w / h) - (1600 / 900)) < 1e-6


def test_snap_resize_mode_ignores_aspect(rgb_portrait_bytes):
    # Resize mode must hit exact dims regardless of input aspect.
    out = dims.snap(rgb_portrait_bytes, (580, 200), mode="resize")
    with Image.open(BytesIO(out)) as img:
        assert img.size == (580, 200)


def test_snap_invalid_mode_raises(rgb_portrait_bytes):
    with pytest.raises(ValueError):
        dims.snap(rgb_portrait_bytes, (100, 100), mode="bogus")  # type: ignore[arg-type]


def test_snap_invalid_target_raises(rgb_portrait_bytes):
    with pytest.raises(ValueError):
        dims.snap(rgb_portrait_bytes, (0, 100))
    with pytest.raises(ValueError):
        dims.snap(rgb_portrait_bytes, (100, -1))


# ---------------------------------------------------------------------------
# to_white() — alpha preservation + RGB whitening
# ---------------------------------------------------------------------------


def test_to_white_preserves_alpha_channel(rgba_logo_bytes):
    out = dims.to_white(rgba_logo_bytes)
    with Image.open(BytesIO(rgba_logo_bytes)) as src, Image.open(BytesIO(out)) as dst:
        assert dst.mode == "RGBA"
        assert dst.size == src.size
        src_alpha = list(src.split()[3].getdata())
        dst_alpha = list(dst.split()[3].getdata())
        assert src_alpha == dst_alpha, "alpha channel must be byte-identical"


def test_to_white_sets_visible_pixels_to_white(rgba_logo_bytes):
    out = dims.to_white(rgba_logo_bytes)
    with Image.open(BytesIO(out)) as img:
        # Center is fully opaque red in the source; should be white now.
        cr, cg, cb, ca = img.getpixel((300, 100))
        assert (cr, cg, cb) == (255, 255, 255)
        assert ca > 0, "center pixel alpha should still be non-zero"


def test_to_white_keeps_transparent_pixels_transparent(rgba_logo_bytes):
    out = dims.to_white(rgba_logo_bytes)
    with Image.open(BytesIO(out)) as img:
        # Corner (0,0) is fully transparent in the source.
        _r, _g, _b, a = img.getpixel((0, 0))
        assert a == 0


def test_to_white_preserves_soft_edges(rgba_logo_bytes):
    out = dims.to_white(rgba_logo_bytes)
    with Image.open(BytesIO(out)) as img:
        # Pixel one step inside the bottom edge has partial alpha.
        _r, _g, _b, a = img.getpixel((81, 41))
        assert 0 < a < 255, f"expected soft-edge alpha, got {a}"


# ---------------------------------------------------------------------------
# chroma_key_magenta()
# ---------------------------------------------------------------------------


def test_chroma_key_magenta_converts_magenta_to_transparent(magenta_keyed_bytes):
    out = dims.chroma_key_magenta(magenta_keyed_bytes)
    with Image.open(BytesIO(out)) as img:
        assert img.mode == "RGBA"
        # A pixel that started magenta must be transparent now.
        _r, _g, _b, a = img.getpixel((0, 0))
        assert a == 0
        # A non-magenta pixel (the black square) must remain opaque.
        _r2, _g2, _b2, a2 = img.getpixel((50, 50))
        assert a2 == 255


def test_chroma_key_magenta_respects_tolerance():
    # Build a pixel that is close-but-not-equal to magenta.
    img = Image.new("RGB", (3, 1), color=(245, 10, 250))  # within tolerance=30
    buf = BytesIO()
    img.save(buf, format="PNG")
    out = dims.chroma_key_magenta(buf.getvalue(), tolerance=30)
    with Image.open(BytesIO(out)) as rgba:
        _r, _g, _b, a = rgba.getpixel((0, 0))
        assert a == 0, "near-magenta within tolerance should key out"

    # Outside tolerance — should remain opaque.
    out2 = dims.chroma_key_magenta(buf.getvalue(), tolerance=2)
    with Image.open(BytesIO(out2)) as rgba2:
        _r, _g, _b, a = rgba2.getpixel((0, 0))
        assert a == 255
