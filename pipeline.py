"""pipeline.py — 11-output orchestration for OTT poster automation.

Implements STEP B–J from ARCHITECTURE.md §2. STEP A (language detection +
translation) is intentionally skipped here because the Streamlit UI now
collects all three titles up-front (see PRD §7.1). The ``translate``
module is still used by ``app.py`` behind the "Fill the rest with AI" button.

Public surface:
    process(input_path, titles, model_id=None, progress_cb=None) -> dict

The function produces up to 11 PNG outputs at the exact pixel specs from
``config.specs`` (see PRD Appendix A). Per-call retries live inside
``gemini.py``; if a whole step still fails we record it in
``_meta.json::failures`` and continue so the operator gets every output
we *can* produce.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Literal

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

import dims
import gemini


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "config.yaml"

LANGS: tuple[str, ...] = ("kr", "en", "zh")
TOTAL_STEPS = 11  # for progress callback denominator
MAX_PARALLEL = 3

# Default config values (used if config.yaml is missing or malformed).
_DEFAULT_FILENAME_TEMPLATE = "{lang}-{type}-title.png"
_DEFAULT_OUTPUT_FOLDER = "ready/{slug}/"

# Per-output cost (USD) for the default Gemini 2.5 Flash Image model.
_DEFAULT_COST_PER_IMAGE = 0.04

# Mapping output number -> (lang, type, variant, spec_key).
# Names follow the convention: {lang}-{type}-title.png
#   lang  ∈ kr | en | cn | clean
#   type  ∈ portrait | landscape | logo | main-banner
# spec_key indexes into dims.SPECS for pixel dimensions ("title" = logo).
OutputSpec = tuple[str, str, str, str]
_OUTPUT_SPECS: dict[int, OutputSpec] = {
    1:  ("kr",    "portrait",    "", "portrait"),
    2:  ("en",    "portrait",    "", "portrait"),
    3:  ("cn",    "portrait",    "", "portrait"),
    4:  ("kr",    "landscape",   "", "landscape"),
    5:  ("en",    "landscape",   "", "landscape"),
    6:  ("cn",    "landscape",   "", "landscape"),
    7:  ("clean", "landscape",   "", "landscape"),
    8:  ("kr",    "logo",        "", "title"),
    9:  ("en",    "logo",        "", "title"),
    10: ("cn",    "logo",        "", "title"),
    11: ("cn",    "main-banner", "", "banner"),
}

_LANG_LABEL_TO_TITLE_KEY: dict[str, str] = {"kr": "kr", "en": "en", "cn": "zh"}

# Logger
_logger = logging.getLogger("pipeline")
if not _logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("[pipeline] %(message)s"))
    _logger.addHandler(_h)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _load_config() -> dict[str, Any]:
    """Load config.yaml; return empty dict on any failure."""
    if yaml is None or not CONFIG_PATH.exists():
        return {}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001
        _logger.warning("config.yaml read failed (%s); using defaults", e)
        return {}


def _resolve_specs(cfg: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """Merge config.yaml specs over dims.SPECS defaults."""
    out: dict[str, tuple[int, int]] = dict(dims.SPECS)
    raw = cfg.get("specs") or {}
    for k, v in raw.items():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            out[k] = (int(v[0]), int(v[1]))
    return out


def _cost_per_image(cfg: dict[str, Any], model_id: str) -> float:
    """Look up cost_per_image for `model_id` in config; fall back to default."""
    models = cfg.get("models") or {}
    for entry in models.get("available") or []:
        if isinstance(entry, dict) and entry.get("id") == model_id:
            try:
                return float(entry.get("cost_per_image", _DEFAULT_COST_PER_IMAGE))
            except (TypeError, ValueError):
                return _DEFAULT_COST_PER_IMAGE
    return _DEFAULT_COST_PER_IMAGE


# ---------------------------------------------------------------------------
# Slug + filename helpers
# ---------------------------------------------------------------------------


# Keep ASCII alphanumerics + CJK ranges; everything else becomes "_".
_SLUG_KEEP_RE = re.compile(
    r"[^"
    r"0-9a-z"
    r"가-힣"      # Hangul Syllables
    r"㄰-㆏"      # Hangul Compatibility Jamo
    r"一-鿿"      # CJK Unified Ideographs
    r"぀-ゟ"      # Hiragana
    r"゠-ヿ"      # Katakana
    r"]+"
)


def _make_slug(title: str, max_len: int = 40) -> str:
    """Generate a filesystem-safe slug from a title.

    Lowercases ASCII, preserves CJK, collapses whitespace and other
    punctuation to underscores, trims to `max_len`. Never returns empty.
    """
    s = (title or "").strip().lower()
    s = _SLUG_KEEP_RE.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "untitled"
    return s[:max_len].rstrip("_") or "untitled"


def _pick_slug_source(titles: dict[str, str]) -> str:
    """Pick a title for slug generation, preferring EN for ASCII filenames."""
    for lang in ("en", "kr", "zh"):
        v = (titles.get(lang) or "").strip()
        if v:
            return v
    # Any value, if all the standard keys are unexpectedly empty.
    for v in titles.values():
        if v and v.strip():
            return v.strip()
    return "untitled"


def _format_filename(template: str, seq: int, lang: str, type_: str,
                     variant: str, slug: str) -> str:
    """Render `filename_template` from config.yaml with str.format."""
    variant_suffix = f"_{variant}" if variant else ""
    date_str = datetime.now().strftime("%Y%m%d")
    try:
        return template.format(
            seq=seq,
            lang=lang,
            type=type_,
            variant=variant,
            variant_suffix=variant_suffix,
            slug=slug,
            date=date_str,
        )
    except (KeyError, IndexError) as e:
        _logger.warning(
            "filename_template %r failed (%s); falling back to default",
            template, e,
        )
        return _DEFAULT_FILENAME_TEMPLATE.format(
            seq=seq, lang=lang, type=type_,
            variant_suffix=variant_suffix,
        )


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _read_input(input_path: Path | bytes | str) -> bytes:
    """Accept a Path, str path, or raw bytes (per ARCHITECTURE §8 v2 hook).

    Raises ValueError for zero-byte input — callers should surface this as
    a user-facing error before any pipeline step runs.
    """
    if isinstance(input_path, bytes):
        data = input_path
    elif isinstance(input_path, (str, Path)):
        data = Path(input_path).read_bytes()
    else:
        raise TypeError(
            f"input_path must be Path | str | bytes, got {type(input_path).__name__}"
        )
    if not data:
        raise ValueError("input is empty (0 bytes) — supply a valid poster image")
    return data


def _input_filename(input_path: Path | bytes | str) -> str:
    """Best-effort source filename for _meta.json."""
    if isinstance(input_path, (str, Path)):
        return str(input_path)
    return "<bytes>"


# ---------------------------------------------------------------------------
# Step runner with structured failure tracking
# ---------------------------------------------------------------------------


def _run_step(
    label: str,
    fn: Callable[[], bytes],
    failures: list[dict[str, Any]],
) -> bytes | None:
    """Run a single Gemini-producing step; record failure and return None on error.

    `gemini.py` already does per-call retries; this just decides whether
    the whole pipeline keeps going.
    """
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - we want every failure recorded
        _logger.error("step %s failed: %s", label, e)
        failures.append({"step": label, "error": str(e)})
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def process(
    input_path: Path | bytes | str,
    titles: dict[str, str],
    model_id: str | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
    slug: str | None = None,
    prompt_overrides: dict[str, str] | None = None,
    precleaned: bytes | None = None,
    style_notes: str | None = None,
) -> dict[str, Any]:
    """Orchestrate the full 11-output poster pipeline for a single title.

    Args:
        input_path: A filesystem path (str or ``Path``) to the source
            portrait poster, or the raw image bytes. Bytes input enables
            the v2 Drive-download path described in ARCHITECTURE §8.
        titles: ``{"kr": str, "en": str, "zh": str}`` — all three keys
            must be present and non-empty. The Streamlit UI guarantees
            this (PRD §7.1); callers from other surfaces must call
            ``translate.translate`` themselves first.
        model_id: Optional override for the Gemini image model. Defaults
            to ``models.default`` from ``config.yaml``.
        progress_cb: Optional ``(current, total, message)`` callback fired
            once after each of the 11 outputs is finalized. ``total`` is
            always 11 (``TOTAL_STEPS``) so the Streamlit progress bar can
            render directly.
        slug: Optional explicit slug; otherwise derived from the EN title
            (or whichever title is present), kept ASCII/CJK + 40 chars.
        prompt_overrides: Optional dict to override the 5 prompt templates.
            Keys: "clean", "outpaint_landscape", "outpaint_banner",
            "title_swap", "title_extract". Missing/empty values fall back
            to the corresponding prompts/*.txt file. The Streamlit UI's
            "Edit prompts (advanced)" section populates this.
        precleaned: Optional pre-approved clean (text-less) portrait bytes.
            When provided, STEP B (gemini.clean) is skipped — used by the
            staged-review UI after the designer approves the clean preview,
            and when the designer uploads their own cleaned poster.
        style_notes: Optional free-form designer instructions appended to
            every title_swap / title_extract prompt for this run.

    Returns:
        ``{"slug": str, "outputs": {int: Path}, "meta": dict}`` where
        ``outputs`` only contains keys for outputs that were successfully
        produced. ``meta`` mirrors the JSON written to ``_meta.json``.
    """

    # ---- Validate inputs --------------------------------------------------
    if not isinstance(titles, dict):
        raise TypeError("titles must be a dict with keys 'kr', 'en', 'zh'")
    missing = [k for k in LANGS if not (titles.get(k) or "").strip()]
    if missing:
        raise ValueError(
            f"titles missing for languages: {missing}; "
            "all of {'kr','en','zh'} must be supplied"
        )
    titles = {k: titles[k].strip() for k in LANGS}

    # Normalize prompt_overrides: empty/whitespace -> None so gemini.py falls back.
    _po = prompt_overrides or {}
    po: dict[str, str | None] = {}
    for _k in ("clean", "outpaint_landscape", "outpaint_banner",
               "title_swap", "title_extract"):
        _v = _po.get(_k)
        po[_k] = _v.strip() if isinstance(_v, str) and _v.strip() else None

    started_at = datetime.now(timezone.utc)
    t_start = time.monotonic()

    cfg = _load_config()
    specs = _resolve_specs(cfg)
    filename_template = str(cfg.get("filename_template") or _DEFAULT_FILENAME_TEMPLATE)
    output_folder_tpl = str(cfg.get("output_folder") or _DEFAULT_OUTPUT_FOLDER)
    resolved_model = model_id or (cfg.get("models") or {}).get("default") \
        or gemini.DEFAULT_MODEL_ID
    cost_per_image = _cost_per_image(cfg, resolved_model)

    # ---- Slug + output dir -----------------------------------------------
    final_slug = slug or _make_slug(_pick_slug_source(titles))
    try:
        out_dir_str = output_folder_tpl.format(slug=final_slug)
    except (KeyError, IndexError):
        out_dir_str = _DEFAULT_OUTPUT_FOLDER.format(slug=final_slug)
    out_dir = Path(out_dir_str)
    if not out_dir.is_absolute():
        out_dir = Path(__file__).parent / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Clear stale outputs from a prior run of this slug so the folder only
    # ever reflects the current run (a designer browsing ready/<slug>/ should
    # never see a leftover PNG from an earlier, partially-failed run).
    # Only top-level files are removed; _work/ is preserved (rewritten below).
    # regenerate() overwrites a single file in place and is unaffected.
    try:
        for old in out_dir.glob("*.png"):
            if old.is_file():
                old.unlink()
        stale_meta = out_dir / "_meta.json"
        if stale_meta.exists():
            stale_meta.unlink()
    except OSError as e:
        _logger.warning("failed to clear stale outputs in %s: %s", out_dir, e)

    # ---- Read input ------------------------------------------------------
    source_bytes = _read_input(input_path)

    # ---- Bookkeeping -----------------------------------------------------
    failures: list[dict[str, Any]] = []
    image_bytes_by_id: dict[int, bytes] = {}   # raw bytes from Gemini/PIL
    output_paths: dict[int, Path] = {}
    progress_counter = {"n": 0}
    api_calls = {"gemini_image": 0}

    def _emit(message: str) -> None:
        progress_counter["n"] += 1
        if progress_cb is not None:
            try:
                progress_cb(progress_counter["n"], TOTAL_STEPS, message)
            except Exception as e:  # noqa: BLE001 - never let UI break pipeline
                _logger.warning("progress_cb raised: %s", e)

    def _record(seq: int, raw_bytes: bytes) -> None:
        """Snap to spec, write to disk, register in output_paths."""
        lang, type_, variant, spec_key = _OUTPUT_SPECS[seq]
        target = specs.get(spec_key) or dims.SPECS[spec_key]
        # For title logos (alpha), use 'resize' to skip aspect cropping. The
        # logo's spec_key is "title" (type_ is "logo"), so key off spec_key.
        snap_mode: Literal["crop", "resize"] = "resize" if spec_key == "title" else "crop"
        try:
            snapped = dims.snap(raw_bytes, target, mode=snap_mode)
        except Exception as e:  # noqa: BLE001
            _logger.error("snap failed for #%d: %s", seq, e)
            failures.append({"step": f"snap_{seq:02d}", "error": str(e)})
            return
        filename = _format_filename(
            filename_template, seq, lang, type_, variant, final_slug
        )
        path = out_dir / filename
        path.write_bytes(snapped)
        output_paths[seq] = path
        _emit(f"#{seq} {lang} {type_}{(' ' + variant) if variant else ''} saved")

    # =====================================================================
    # STEP B — clean_portrait (1 call). Required for almost everything.
    # Skipped when the caller supplies an approved `precleaned` image
    # (staged-review flow / designer-provided clean base).
    # =====================================================================
    if precleaned is not None:
        _logger.info("STEP B: skipped (precleaned supplied)")
        clean_portrait = precleaned
    else:
        _logger.info("STEP B: gemini.clean(portrait)")
        clean_portrait = _run_step(
            "B_clean_portrait",
            lambda: gemini.clean(source_bytes, model_id=resolved_model,
                                 prompt_override=po["clean"]),
            failures,
        )
        if clean_portrait is not None:
            api_calls["gemini_image"] += 1

    # Persist intermediates so single outputs can be regenerated later
    # without re-running the whole pipeline (see regenerate()).
    work_dir = out_dir / "_work"
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "source.png").write_bytes(source_bytes)
        if clean_portrait is not None:
            (work_dir / "clean_portrait.png").write_bytes(clean_portrait)
    except OSError as e:
        _logger.warning("failed to persist _work intermediates: %s", e)

    # =====================================================================
    # STEP C (3 parallel) — portrait title swaps -> outputs 1, 2, 3
    # STEP D (1)          — clean landscape outpaint -> output 7
    #
    # IMPORTANT: STEP C uses the ORIGINAL portrait (source_bytes), not
    # clean_portrait. The title_swap prompt is a "replace existing title"
    # pattern — the model needs to see the source title to match its
    # typography/style. STEP D still uses clean_portrait so the outpainted
    # landscape doesn't carry over any text.
    #
    # The two steps are independent and run concurrently.
    # =====================================================================
    portrait_by_lang: dict[str, bytes] = {}
    clean_landscape: bytes | None = None

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL + 1) as ex:
        future_to_label: dict[Any, tuple[str, str]] = {}

        # STEP C — always run; uses original portrait (not clean_portrait).
        for lang in LANGS:
            fut = ex.submit(
                gemini.title_swap,
                source_bytes, titles[lang], lang,
                model_id=resolved_model,
                prompt_override=po["title_swap"],
                style_notes=style_notes,
            )
            future_to_label[fut] = ("portrait_swap", lang)

        # STEP D — only run if clean_portrait succeeded.
        d_label = "D_outpaint_landscape"
        if clean_portrait is not None:
            fut_d = ex.submit(
                gemini.outpaint,
                clean_portrait, "landscape",
                model_id=resolved_model,
                prompt_override=po["outpaint_landscape"],
            )
            future_to_label[fut_d] = ("landscape_outpaint", "")
        else:
            failures.append({
                "step": d_label,
                "error": "clean_portrait unavailable; landscape outpaint skipped",
            })

        for fut in as_completed(future_to_label):
            kind, lang = future_to_label[fut]
            try:
                result = fut.result()
            except Exception as e:  # noqa: BLE001
                full_label = (f"C_title_swap_portrait_{lang}" if kind == "portrait_swap"
                              else d_label)
                _logger.error("step %s failed: %s", full_label, e)
                failures.append({"step": full_label, "error": str(e)})
                continue

            api_calls["gemini_image"] += 1
            if kind == "portrait_swap":
                portrait_by_lang[lang] = result
            else:
                clean_landscape = result

    # Record outputs 1, 2, 3 in seq order regardless of completion order.
    seq_for_lang = {"kr": 1, "en": 2, "zh": 3}
    for lang, seq in seq_for_lang.items():
        if lang in portrait_by_lang:
            _record(seq, portrait_by_lang[lang])

    # Output 7: clean landscape
    if clean_landscape is not None:
        _record(7, clean_landscape)

    # =====================================================================
    # STEP E (3 parallel) — landscape title swaps -> outputs 4, 5, 6
    # STEP F (1)          — CN banner outpaint (clean) -> output 11
    # Both depend on clean_landscape.
    # =====================================================================
    landscape_by_lang: dict[str, bytes] = {}
    banner_clean: bytes | None = None

    if clean_landscape is not None:
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL + 1) as ex:
            future_to_label = {}

            for lang in LANGS:
                fut = ex.submit(
                    gemini.title_swap,
                    clean_landscape, titles[lang], lang,
                    model_id=resolved_model,
                    prompt_override=po["title_swap"],
                    # Reference: original portrait still carries the source-language
                    # title; the model copies its font/color into the new language.
                    # Without this, EN/CN landscape titles drift in typography
                    # because clean_landscape has no title to match.
                    reference_bytes=source_bytes,
                    style_notes=style_notes,
                )
                future_to_label[fut] = ("landscape_swap", lang)

            fut_f = ex.submit(
                gemini.outpaint,
                clean_landscape, "banner",
                model_id=resolved_model,
                prompt_override=po["outpaint_banner"],
            )
            future_to_label[fut_f] = ("banner_outpaint", "")

            for fut in as_completed(future_to_label):
                kind, lang = future_to_label[fut]
                try:
                    result = fut.result()
                except Exception as e:  # noqa: BLE001
                    full_label = (f"E_title_swap_landscape_{lang}" if kind == "landscape_swap"
                                  else "F_outpaint_banner")
                    _logger.error("step %s failed: %s", full_label, e)
                    failures.append({"step": full_label, "error": str(e)})
                    continue

                api_calls["gemini_image"] += 1
                if kind == "landscape_swap":
                    landscape_by_lang[lang] = result
                else:
                    banner_clean = result
    else:
        failures.append({
            "step": "E_F_skipped",
            "error": "clean_landscape unavailable; skipped landscape swaps + banner outpaint",
        })

    seq_for_lang_landscape = {"kr": 4, "en": 5, "zh": 6}
    for lang, seq in seq_for_lang_landscape.items():
        if lang in landscape_by_lang:
            _record(seq, landscape_by_lang[lang])

    if banner_clean is not None:
        _record(11, banner_clean)

    # =====================================================================
    # STEP G (3 parallel) — title logos -> outputs 8 (kr), 9 (en), 10 (cn).
    # Source: per-lang portrait posters from STEP C.
    # Reference: original portrait (source_bytes) so the model can copy
    # the ORIGINAL title's color/font exactly when re-extracting in any
    # language — addresses non-Latin scripts (KR/CN) drifting in color.
    # =====================================================================
    color_logos_by_lang: dict[str, bytes] = {}
    color_seq_for_lang = {"kr": 8, "en": 9, "zh": 10}

    eligible_langs = [l for l in LANGS if l in portrait_by_lang]
    if eligible_langs:
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
            fut_to_lang: dict[Any, str] = {}
            for lang in eligible_langs:
                fut = ex.submit(
                    gemini.title_extract,
                    portrait_by_lang[lang], titles[lang], lang,
                    model_id=resolved_model,
                    prompt_override=po["title_extract"],
                    reference_bytes=source_bytes,
                    style_notes=style_notes,
                )
                fut_to_lang[fut] = lang

            for fut in as_completed(fut_to_lang):
                lang = fut_to_lang[fut]
                try:
                    result = fut.result()
                except Exception as e:  # noqa: BLE001
                    _logger.error("step G_title_extract_%s failed: %s", lang, e)
                    failures.append({
                        "step": f"G_title_extract_{lang}", "error": str(e),
                    })
                    continue
                api_calls["gemini_image"] += 1
                # gemini.title_extract returns flat #FF00FF magenta bg; chroma-key it.
                try:
                    rgba = dims.chroma_key_magenta(result, fallback_auto=True)
                except Exception as e:  # noqa: BLE001
                    _logger.error("chroma_key failed for %s: %s", lang, e)
                    failures.append({
                        "step": f"G_chroma_key_{lang}", "error": str(e),
                    })
                    continue
                color_logos_by_lang[lang] = rgba

    for lang, seq in color_seq_for_lang.items():
        if lang in color_logos_by_lang:
            _record(seq, color_logos_by_lang[lang])

    # =====================================================================
    # STEP J — write _meta.json
    # =====================================================================
    finished_at = datetime.now(timezone.utc)
    duration_sec = round(time.monotonic() - t_start, 2)

    outputs_meta: dict[str, dict[str, Any]] = {}
    for seq in sorted(_OUTPUT_SPECS):
        lang, type_, variant, spec_key = _OUTPUT_SPECS[seq]
        target = specs.get(spec_key) or dims.SPECS[spec_key]
        if seq in output_paths:
            outputs_meta[str(seq)] = {
                "path": str(output_paths[seq]),
                "dim": [target[0], target[1]],
                "lang": lang,
                "type": type_,
                "variant": variant,
                "status": "ok",
            }
        else:
            outputs_meta[str(seq)] = {
                "path": None,
                "dim": [target[0], target[1]],
                "lang": lang,
                "type": type_,
                "variant": variant,
                "status": "failed",
            }

    meta: dict[str, Any] = {
        "slug": final_slug,
        "source": "local",  # v2 may set 'sheet'
        "input": {
            "file": _input_filename(input_path),
            "titles": titles,
            "style_notes": (style_notes or "").strip(),
        },
        "model": {
            "image_model": resolved_model,
            "cost_per_image_usd": cost_per_image,
        },
        "outputs": outputs_meta,
        "stats": {
            "started_at": started_at.isoformat().replace("+00:00", "Z"),
            "finished_at": finished_at.isoformat().replace("+00:00", "Z"),
            "duration_sec": duration_sec,
            "api_calls": api_calls,
            "estimated_cost_usd": round(
                api_calls["gemini_image"] * cost_per_image, 4
            ),
            "outputs_produced": len(output_paths),
            "outputs_expected": TOTAL_STEPS,
        },
        "failures": failures,
    }

    meta_path = out_dir / "_meta.json"
    try:
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        _logger.error("failed to write _meta.json: %s", e)

    _logger.info(
        "done slug=%s produced=%d/%d failures=%d duration=%.1fs",
        final_slug, len(output_paths), TOTAL_STEPS, len(failures), duration_sec,
    )

    return {"slug": final_slug, "outputs": output_paths, "meta": meta}


# ---------------------------------------------------------------------------
# Staged execution + single-output regeneration
# ---------------------------------------------------------------------------


def stage_clean(
    input_path: Path | bytes | str,
    model_id: str | None = None,
    prompt_override: str | None = None,
) -> bytes:
    """Run only STEP B (text removal) and return the clean portrait bytes.

    Used by the staged-review UI: the designer inspects/retries this result
    before committing to the remaining 11 API calls. Costs 1 image call.
    Raises on failure (no failure-list accumulation here — the caller shows
    the error directly).
    """
    cfg = _load_config()
    resolved_model = model_id or (cfg.get("models") or {}).get("default") \
        or gemini.DEFAULT_MODEL_ID
    source_bytes = _read_input(input_path)
    return gemini.clean(source_bytes, model_id=resolved_model,
                        prompt_override=prompt_override)


# Which intermediate each output is generated from, for regenerate().
#   portrait swaps (1-3)   <- _work/source.png
#   clean landscape (7)    <- _work/clean_portrait.png
#   landscape swaps (4-6)  <- output #7 file (+ source as typography reference)
#   banner (11)            <- output #7 file
#   logos (8-10)           <- output #1/2/3 file (+ source as reference)
_REGEN_PORTRAIT_SEQ_FOR_LOGO = {8: 1, 9: 2, 10: 3}


def regenerate(
    out_dir: Path | str,
    seq: int,
    model_id: str | None = None,
    prompt_overrides: dict[str, str] | None = None,
    style_notes: str | None = None,
) -> Path:
    """Re-run the single API call behind output `seq` and overwrite its file.

    Reads titles/model/style_notes from ``_meta.json`` (UI args override).
    Requires the ``_work/`` intermediates persisted by ``process()`` and,
    for dependent outputs, the upstream output files on disk.

    Note: regenerating #7 (clean landscape) does NOT cascade — outputs
    4/5/6/11 keep their old bases until individually regenerated. The UI
    surfaces this to the designer.

    Returns the path of the rewritten output file. Raises RuntimeError with
    a designer-readable message when prerequisites are missing.
    """
    out_dir = Path(out_dir)
    if seq not in _OUTPUT_SPECS:
        raise ValueError(f"unknown output seq {seq}; must be 1-11")

    meta_path = out_dir / "_meta.json"
    if not meta_path.exists():
        raise RuntimeError(f"_meta.json not found: {out_dir} — run a full generation first")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    titles: dict[str, str] = (meta.get("input") or {}).get("titles") or {}
    resolved_model = model_id or (meta.get("model") or {}).get("image_model") \
        or (( _load_config().get("models") or {}).get("default")) \
        or gemini.DEFAULT_MODEL_ID
    notes = style_notes if style_notes is not None \
        else (meta.get("input") or {}).get("style_notes") or None

    _po = prompt_overrides or {}
    def _ov(key: str) -> str | None:
        v = _po.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else None

    work_dir = out_dir / "_work"
    source_path = work_dir / "source.png"
    if not source_path.exists():
        raise RuntimeError(
            "_work/source.png not found — outputs from older runs do not support "
            "per-output regeneration. Please run a full generation again."
        )
    source_bytes = source_path.read_bytes()

    def _output_bytes(dep_seq: int) -> bytes:
        entry = (meta.get("outputs") or {}).get(str(dep_seq)) or {}
        p = entry.get("path")
        if not p or not Path(p).exists():
            raise RuntimeError(
                f"Regenerating #{seq} requires output #{dep_seq}, but its file is missing — "
                f"please regenerate #{dep_seq} first."
            )
        return Path(p).read_bytes()

    lang_label, type_, variant, spec_key = _OUTPUT_SPECS[seq]
    title_key = _LANG_LABEL_TO_TITLE_KEY.get(lang_label, lang_label)
    title = (titles.get(title_key) or "").strip()

    # ---- The one API call -------------------------------------------------
    if seq in (1, 2, 3):  # portrait title swap from the original source
        raw = gemini.title_swap(
            source_bytes, title, title_key,
            model_id=resolved_model,
            prompt_override=_ov("title_swap"),
            style_notes=notes,
        )
    elif seq == 7:  # clean landscape outpaint from clean portrait
        clean_path = work_dir / "clean_portrait.png"
        if not clean_path.exists():
            raise RuntimeError(
                "_work/clean_portrait.png not found — please run a full generation again."
            )
        raw = gemini.outpaint(
            clean_path.read_bytes(), "landscape",
            model_id=resolved_model,
            prompt_override=_ov("outpaint_landscape"),
        )
    elif seq in (4, 5, 6):  # landscape title swap from clean landscape (#7)
        raw = gemini.title_swap(
            _output_bytes(7), title, title_key,
            model_id=resolved_model,
            prompt_override=_ov("title_swap"),
            reference_bytes=source_bytes,
            style_notes=notes,
        )
    elif seq == 11:  # banner outpaint from clean landscape (#7)
        raw = gemini.outpaint(
            _output_bytes(7), "banner",
            model_id=resolved_model,
            prompt_override=_ov("outpaint_banner"),
        )
    else:  # 8, 9, 10 — logo extract from the matching portrait output
        raw = gemini.title_extract(
            _output_bytes(_REGEN_PORTRAIT_SEQ_FOR_LOGO[seq]), title, title_key,
            model_id=resolved_model,
            prompt_override=_ov("title_extract"),
            reference_bytes=source_bytes,
            style_notes=notes,
        )
        raw = dims.chroma_key_magenta(raw, fallback_auto=True)

    # ---- Snap + overwrite the existing output file -------------------------
    cfg = _load_config()
    specs = _resolve_specs(cfg)
    target = specs.get(spec_key) or dims.SPECS[spec_key]
    snap_mode: Literal["crop", "resize"] = "resize" if spec_key == "title" else "crop"
    snapped = dims.snap(raw, target, mode=snap_mode)

    entry = (meta.get("outputs") or {}).get(str(seq)) or {}
    if entry.get("path"):
        path = Path(entry["path"])
    else:
        filename_template = str(cfg.get("filename_template") or _DEFAULT_FILENAME_TEMPLATE)
        path = out_dir / _format_filename(
            filename_template, seq, lang_label, type_, variant,
            meta.get("slug") or out_dir.name,
        )
    path.write_bytes(snapped)

    # ---- Update _meta.json ------------------------------------------------
    meta.setdefault("outputs", {})[str(seq)] = {
        "path": str(path),
        "dim": [target[0], target[1]],
        "lang": lang_label,
        "type": type_,
        "variant": variant,
        "status": "ok",
    }
    stats = meta.setdefault("stats", {})
    stats["regenerations"] = int(stats.get("regenerations") or 0) + 1
    try:
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as e:
        _logger.warning("failed to update _meta.json after regenerate: %s", e)

    _logger.info("regenerated #%d -> %s", seq, path)
    return path


# ---------------------------------------------------------------------------
# Manual smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="pipeline.py manual run")
    ap.add_argument("image", type=Path, help="input portrait poster")
    ap.add_argument("--kr", required=True)
    ap.add_argument("--en", required=True)
    ap.add_argument("--zh", required=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--slug", default=None)
    args = ap.parse_args()

    def _cb(cur: int, total: int, msg: str) -> None:
        print(f"  [{cur:>2}/{total}] {msg}")

    result = process(
        args.image,
        {"kr": args.kr, "en": args.en, "zh": args.zh},
        model_id=args.model,
        progress_cb=_cb,
        slug=args.slug,
    )
    print(f"\nslug: {result['slug']}")
    print(f"outputs: {len(result['outputs'])}/{TOTAL_STEPS}")
    for seq in sorted(result["outputs"]):
        print(f"  #{seq:>2}: {result['outputs'][seq]}")
    if result["meta"]["failures"]:
        print("\nfailures:")
        for f in result["meta"]["failures"]:
            print(f"  - {f['step']}: {f['error']}")
