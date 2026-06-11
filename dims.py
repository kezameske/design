"""dims.py — PIL-based exact pixel snapping and alpha color utilities.

Per ARCHITECTURE.md §3, this module provides:
- snap(): center-crop + Lanczos resize to exact target dimensions
- to_white(): convert RGBA logo RGB channels to white while preserving alpha
- chroma_key_magenta(): convert magenta background to transparent (fallback for title_extract)
- SPECS: canonical output pixel dimensions

All I/O is via bytes (no file paths) using BytesIO.
"""

from __future__ import annotations

from io import BytesIO
from typing import Literal

from PIL import Image


SPECS: dict[str, tuple[int, int]] = {
    "portrait":  (900, 1600),
    "landscape": (1600, 900),
    "title":     (580, 200),
    "banner":    (1520, 536),
}


def snap(image_bytes: bytes, target: tuple[int, int],
         mode: Literal["crop", "resize"] = "crop") -> bytes:
    """Snap image to exact pixel dimensions.

    - mode='crop': center-crop to target aspect ratio, then Lanczos resize.
    - mode='resize': direct Lanczos resize (no aspect preservation) — for logos.

    Preserves RGBA if present; otherwise returns RGB PNG bytes.
    """
    tw, th = target
    if tw <= 0 or th <= 0:
        raise ValueError(f"snap(): target dimensions must be positive, got {target}")

    with Image.open(BytesIO(image_bytes)) as img:
        # Preserve alpha if present; otherwise standard RGB.
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")

        if mode == "resize":
            out = img.resize((tw, th), Image.Resampling.LANCZOS)
        elif mode == "crop":
            src_w, src_h = img.size
            target_ratio = tw / th
            src_ratio = src_w / src_h

            if src_ratio > target_ratio:
                # Source is wider than target — crop horizontal sides.
                new_w = int(round(src_h * target_ratio))
                left = (src_w - new_w) // 2
                box = (left, 0, left + new_w, src_h)
            else:
                # Source is taller than target — crop top/bottom.
                new_h = int(round(src_w / target_ratio))
                top = (src_h - new_h) // 2
                box = (0, top, src_w, top + new_h)

            cropped = img.crop(box)
            out = cropped.resize((tw, th), Image.Resampling.LANCZOS)
        else:
            raise ValueError(f"snap(): mode must be 'crop' or 'resize', got {mode!r}")

        buf = BytesIO()
        out.save(buf, format="PNG")
        return buf.getvalue()


def to_white(rgba_png_bytes: bytes) -> bytes:
    """Convert RGBA logo to white: keep alpha channel, set RGB to (255,255,255).

    Preserves alpha gradient (soft edges remain soft).
    """
    with Image.open(BytesIO(rgba_png_bytes)) as img:
        img = img.convert("RGBA")
        r, g, b, a = img.split()
        white = Image.new("L", img.size, 255)
        out = Image.merge("RGBA", (white, white, white, a))

        buf = BytesIO()
        out.save(buf, format="PNG")
        return buf.getvalue()


def _median_border_color(img: Image.Image) -> tuple[int, int, int]:
    """Median RGB of the 1px border — the background color of a keyed image."""
    w, h = img.size
    px = img.load()
    rs: list[int] = []
    gs: list[int] = []
    bs: list[int] = []
    coords = (
        [(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)]
        + [(0, y) for y in range(1, h - 1)] + [(w - 1, y) for y in range(1, h - 1)]
    )
    for x, y in coords:
        r, g, b = px[x, y][:3]
        rs.append(r)
        gs.append(g)
        bs.append(b)
    rs.sort()
    gs.sort()
    bs.sort()
    mid = len(rs) // 2
    return rs[mid], gs[mid], bs[mid]


def _is_magenta_like(color: tuple[int, int, int]) -> bool:
    """True if a color is in the magenta/pink family (strong red, weak green).

    Models sometimes drift from the requested #FF00FF toward pink
    (e.g. RGB 254,5,165) — still keyable, never a legitimate title color
    per the title_extract prompt.
    """
    r, g, b = color
    return r >= 180 and g <= 120 and (r - g) >= 100 and (b - g) >= 60


def _key_out(img: Image.Image, key_color: tuple[int, int, int],
             tolerance: int) -> int:
    """Set alpha=0 within `tolerance` of key_color (Chebyshev distance),
    with a soft alpha ramp up to 2×tolerance to reduce edge fringe.
    Mutates `img` in place; returns the count of fully-keyed pixels.
    """
    pixels = img.load()
    kr, kg, kb = key_color
    w, h = img.size
    keyed = 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            dist = max(abs(r - kr), abs(g - kg), abs(b - kb))
            if dist <= tolerance:
                pixels[x, y] = (r, g, b, 0)
                keyed += 1
            elif dist <= tolerance * 2:
                ramp = (dist - tolerance) * 255 // tolerance
                if ramp < a:
                    pixels[x, y] = (r, g, b, ramp)
    return keyed


def chroma_key_magenta(png_bytes: bytes,
                       key_color: tuple[int, int, int] = (255, 0, 255),
                       tolerance: int = 30,
                       fallback_auto: bool = False) -> bytes:
    """Convert magenta-background pixels to transparent.

    Used as fallback when gemini.title_extract returns a magenta-keyed image
    rather than true alpha. Pixels within `tolerance` (Chebyshev distance)
    of `key_color` become fully transparent; pixels up to 2×tolerance get a
    soft alpha ramp so anti-aliased title edges don't show a hard fringe.

    With ``fallback_auto=True``, if keying on `key_color` clears less than
    30% of the image (models sometimes render the background pink instead of
    pure #FF00FF), the median border color is detected and — when it is
    magenta/pink-like — used as the key instead. Returns RGBA PNG bytes.
    """
    with Image.open(BytesIO(png_bytes)) as src:
        img = src.convert("RGBA")

    total = img.size[0] * img.size[1]
    keyed = _key_out(img, key_color, tolerance)

    if fallback_auto and keyed / total < 0.30:
        detected = _median_border_color(img)
        if detected != key_color and _is_magenta_like(detected):
            # Re-key from the original pixels with the detected background.
            with Image.open(BytesIO(png_bytes)) as src:
                retry = src.convert("RGBA")
            # Accept if the detected key clears meaningfully more than the
            # original did (bg can be <30% when the title fills the frame).
            if _key_out(retry, detected, tolerance) / total >= max(0.15, keyed / total * 2):
                img = retry

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":
    # --- Inline self-tests ---
    print("Running dims.py self-tests...")

    # Test 1: synthetic 1920x1080 RGB -> snap to 1600x900
    synth = Image.new("RGB", (1920, 1080), color=(120, 60, 200))
    # Add a visible non-uniform region so resize is not trivially identity.
    for x in range(0, 1920, 100):
        for y in range(1080):
            synth.putpixel((x, y), (255, 255, 0))
    buf = BytesIO()
    synth.save(buf, format="PNG")
    snapped_bytes = snap(buf.getvalue(), (1600, 900), mode="crop")
    with Image.open(BytesIO(snapped_bytes)) as snapped:
        assert snapped.size == (1600, 900), f"snap dim mismatch: {snapped.size}"
        print(f"  [PASS] snap(1920x1080 -> 1600x900) produced {snapped.size}")

    # Test 1b: resize mode
    snapped_resize = snap(buf.getvalue(), (580, 200), mode="resize")
    with Image.open(BytesIO(snapped_resize)) as sr:
        assert sr.size == (580, 200), f"resize dim mismatch: {sr.size}"
        print(f"  [PASS] snap(resize -> 580x200) produced {sr.size}")

    # Test 2: synthetic RGBA logo (red text on transparent) -> to_white
    logo = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    # Draw a "red text" rectangle with soft edges (varied alpha).
    for x in range(50, 250):
        for y in range(30, 70):
            # Edges have soft alpha to verify gradient preservation.
            edge_dist = min(x - 50, 250 - x, y - 30, 70 - y)
            alpha = min(255, edge_dist * 40)
            logo.putpixel((x, y), (220, 30, 30, alpha))
    lbuf = BytesIO()
    logo.save(lbuf, format="PNG")
    white_bytes = to_white(lbuf.getvalue())
    with Image.open(BytesIO(white_bytes)) as w:
        assert w.mode == "RGBA", f"to_white mode mismatch: {w.mode}"
        # Check center pixel: should be white with original alpha.
        cr, cg, cb, ca = w.getpixel((150, 50))
        assert (cr, cg, cb) == (255, 255, 255), f"center RGB not white: {(cr, cg, cb)}"
        assert ca > 0, f"center alpha lost: {ca}"
        # Check transparent region remains transparent.
        tr, tg, tb, ta = w.getpixel((10, 10))
        assert ta == 0, f"transparent region got alpha {ta}"
        # Check soft-edge pixel preserved gradient (non-zero, non-max).
        er, eg, eb, ea = w.getpixel((51, 50))
        assert 0 < ea < 255, f"soft edge alpha not preserved: {ea}"
        assert (er, eg, eb) == (255, 255, 255), f"soft edge RGB not white: {(er, eg, eb)}"
        print(f"  [PASS] to_white(): RGB->white, alpha preserved (center a={ca}, edge a={ea})")

    print("All self-tests passed.")
