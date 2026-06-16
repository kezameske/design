"""app.py — Streamlit UI for the OTT poster automation pipeline.

Entry point for the v1 local web UI described in PRD §7.1.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any

import streamlit as st

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is optional
    pass

import streamlit as _st_bootstrap  # noqa: E402  — bridge st.secrets → os.environ for cloud
for _key in ("OPENROUTER_API_KEY", "GOOGLE_API_KEY", "APP_PASSWORD"):
    if not os.environ.get(_key):
        try:
            _val = _st_bootstrap.secrets.get(_key)
        except (FileNotFoundError, Exception):
            _val = None
        if _val:
            os.environ[_key] = str(_val)

import pipeline
import translate


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_TITLE = "Poster Automation v1"
CONFIG_PATH = Path(__file__).parent / "config.yaml"
INPUTS_DIR = Path(__file__).parent / "inputs"
PROMPTS_DIR = Path(__file__).parent / "prompts"
ENV_EXAMPLE_PATH = Path(__file__).parent / ".env.example"

PROMPT_STEPS: list[tuple[str, str, str]] = [
    # (step_key, label, prompt_file)
    ("clean",              "1. Remove body text (clean)",               "clean.txt"),
    ("outpaint_landscape", "2. Portrait → Landscape conversion",        "outpaint_landscape.txt"),
    ("outpaint_banner",    "3. Landscape → CN Banner conversion (no text)", "outpaint_banner.txt"),
    ("title_swap",         "4. Title compose/swap (placeholders: {title}, {language})", "title_swap.txt"),
    ("title_extract",      "5. Title logo extraction (magenta background)", "title_extract.txt"),
]

LANG_LABELS: dict[str, str] = {
    "kr": "🇰🇷 Korean Title",
    "en": "🇬🇧 English Title",
    "zh": "🇨🇳 Chinese Title (简体)",
}
LANGS: tuple[str, ...] = ("kr", "en", "zh")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def load_config() -> dict[str, Any]:
    """Load config.yaml; return empty dict on failure."""
    if yaml is None or not CONFIG_PATH.exists():
        return {}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def get_model_options(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Return (available_models, default_index) from config."""
    models_cfg = cfg.get("models") or {}
    available = models_cfg.get("available") or []
    default_id = models_cfg.get("default")

    # Normalize entries to dicts with id/label/cost_per_image.
    normalized: list[dict[str, Any]] = []
    for entry in available:
        if isinstance(entry, dict) and entry.get("id"):
            normalized.append(
                {
                    "id": str(entry["id"]),
                    "label": str(entry.get("label", entry["id"])),
                    "cost_per_image": float(entry.get("cost_per_image", 0.04)),
                }
            )

    if not normalized:
        normalized = [
            {
                "id": "google/gemini-2.5-flash-image",
                "label": "Gemini 2.5 Flash Image (Nano Banana)",
                "cost_per_image": 0.04,
            }
        ]

    default_idx = 0
    for i, m in enumerate(normalized):
        if m["id"] == default_id:
            default_idx = i
            break

    return normalized, default_idx


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


def _load_prompt_default(filename: str) -> str:
    """Read a prompts/*.txt file as the default for the editor."""
    p = PROMPTS_DIR / filename
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def init_session_state() -> None:
    """Initialize all session-state keys we read elsewhere."""
    defaults: dict[str, Any] = {
        "uploaded_bytes": None,
        "uploaded_name": None,
        "title_kr": "",
        "title_en": "",
        "title_zh": "",
        "result": None,
        "is_running": False,
        "_pending_fill": False,
        "style_notes": "",
        "clean_preview": None,   # approved-pending clean portrait bytes
        "clean_source": None,    # "ai" | "upload" — provenance of clean_preview
    }
    # Pre-load prompt editor text areas from prompts/*.txt.
    for step_key, _label, filename in PROMPT_STEPS:
        defaults[f"prompt_{step_key}"] = _load_prompt_default(filename)
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ---------------------------------------------------------------------------
# Translation action
# ---------------------------------------------------------------------------


def fill_missing_titles() -> None:
    """Detect language of first filled title and translate to empty fields."""
    current: dict[str, str] = {
        "kr": st.session_state.title_kr.strip(),
        "en": st.session_state.title_en.strip(),
        "zh": st.session_state.title_zh.strip(),
    }
    filled = [(lang, val) for lang, val in current.items() if val]
    if not filled:
        st.warning("Enter at least one title.")
        return

    src_lang, src_value = filled[0]
    targets = [l for l in LANGS if l != src_lang and not current[l]]
    if not targets:
        st.info("All titles are already filled in.")
        return

    try:
        # detect() can refine the source language guess; if it disagrees with
        # the field the user typed into, prefer the user's field as the source.
        try:
            detected = translate.detect(src_value)
            if detected in LANGS and not current[detected]:
                src_lang = detected
                targets = [l for l in LANGS if l != src_lang and not current[l]]
        except Exception:
            # Detection failure is non-fatal; we already have a src guess.
            pass

        translated = translate.translate(src_value, src_lang, targets)
    except Exception as e:  # noqa: BLE001
        st.warning(f"Auto-translation failed: {e} — please enter manually.")
        return

    for lang in targets:
        if lang in translated and translated[lang]:
            st.session_state[f"title_{lang}"] = translated[lang]
    st.success(f"Auto-filled {', '.join(targets).upper()} titles.")


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------


def save_upload_to_disk(name: str, data: bytes) -> Path:
    """Persist the uploaded image into inputs/ so pipeline gets a Path."""
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(name).name or "upload.bin"
    target = INPUTS_DIR / safe_name
    target.write_bytes(data)
    return target


def run_pipeline(
    image_path: Path,
    titles: dict[str, str],
    model_id: str,
    progress_bar: Any,
    status_box: Any,
    prompt_overrides: dict[str, str] | None = None,
    precleaned: bytes | None = None,
    style_notes: str | None = None,
) -> dict[str, Any] | None:
    """Invoke pipeline.process with a Streamlit-bound progress callback."""

    def cb(current: int, total: int, message: str) -> None:
        try:
            progress_bar.progress(min(current / total, 1.0))
            status_box.write(f"{current}/{total} — {message}")
        except Exception:
            # Streamlit can throw if the widget context is gone; swallow it.
            pass

    try:
        return pipeline.process(
            image_path,
            titles,
            model_id=model_id,
            progress_cb=cb,
            prompt_overrides=prompt_overrides,
            precleaned=precleaned,
            style_notes=style_notes,
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"An error occurred while running the pipeline: {e}")
        return None


# ---------------------------------------------------------------------------
# Result rendering
# ---------------------------------------------------------------------------


def build_zip_bytes(result: dict[str, Any]) -> bytes:
    """Bundle all output PNGs + _meta.json into a single ZIP."""
    buf = io.BytesIO()
    outputs: dict[int, Path] = result.get("outputs", {})
    slug = result.get("slug", "outputs")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for seq in sorted(outputs):
            p = outputs[seq]
            if p.exists():
                zf.write(p, arcname=p.name)
        # Locate _meta.json alongside the first output.
        if outputs:
            meta_path = next(iter(outputs.values())).parent / "_meta.json"
            if meta_path.exists():
                zf.write(meta_path, arcname="_meta.json")
        else:
            # Fallback: serialize the meta from the result dict.
            meta_json = json.dumps(
                result.get("meta", {}), ensure_ascii=False, indent=2
            )
            zf.writestr("_meta.json", meta_json)
    buf.seek(0)
    return buf.getvalue()


@st.cache_data(show_spinner=False)
def _upscale_bytes(path_str: str, mtime: float, factor: int) -> bytes:
    """Lanczos-resize an image by `factor` and return PNG bytes. Cached per (path, mtime, factor)."""
    from PIL import Image as PILImage

    with PILImage.open(path_str) as img:
        w, h = img.size
        upscaled = img.resize(
            (w * factor, h * factor), PILImage.Resampling.LANCZOS
        )
        buf = io.BytesIO()
        upscaled.save(buf, format="PNG")
        return buf.getvalue()


@st.dialog("Preview", width="large")
def _preview_dialog(seq: int, path: Path) -> None:
    """Full-size image popup. Press Esc or click outside to close (native)."""
    st.caption(f"#{seq} — {path.name}")
    if not path.exists():
        st.warning(f"File not found: {path}")
        return

    st.image(str(path), width="stretch")

    try:
        from PIL import Image as PILImage
        with PILImage.open(path) as _im:
            w, h = _im.size
        st.caption(
            f"Original {w}×{h} · x2 → {w*2}×{h*2} · x4 → {w*4}×{h*4}  "
            f"(Lanczos resampling, not AI)"
        )
    except Exception:
        pass

    mtime = path.stat().st_mtime
    stem, suffix = path.stem, ".png"

    col1, col2, col3 = st.columns(3)
    with col1:
        st.download_button(
            "Download original",
            data=path.read_bytes(),
            file_name=path.name,
            mime="image/png",
            width="stretch",
            key=f"dl_orig_{seq}",
        )
    with col2:
        st.download_button(
            "x2 upscale",
            data=_upscale_bytes(str(path), mtime, 2),
            file_name=f"{stem}@2x{suffix}",
            mime="image/png",
            width="stretch",
            key=f"dl_2x_{seq}",
        )
    with col3:
        st.download_button(
            "x4 upscale",
            data=_upscale_bytes(str(path), mtime, 4),
            file_name=f"{stem}@4x{suffix}",
            mime="image/png",
            width="stretch",
            key=f"dl_4x_{seq}",
        )


def render_results(
    result: dict[str, Any],
    model_id: str | None = None,
    prompt_overrides: dict[str, str] | None = None,
    style_notes: str | None = None,
) -> None:
    """Render thumbnail grid, cost summary, ZIP download, and folder hint."""
    outputs: dict[int, Path] = result.get("outputs", {})
    meta: dict[str, Any] = result.get("meta", {})
    slug = result.get("slug", "")

    st.subheader("Results")
    produced = len(outputs)
    expected = meta.get("stats", {}).get("outputs_expected", 11)
    st.caption(
        f"{produced}/{expected} outputs generated — click the filename button to enlarge (ESC to close), "
        "use the 🔄 button to regenerate just that output"
    )

    out_dir: Path | None = None
    if outputs:
        out_dir = next(iter(outputs.values())).parent

    if outputs:
        cols = st.columns(4)
        for idx, seq in enumerate(sorted(outputs)):
            path = outputs[seq]
            with cols[idx % 4]:
                if path.exists():
                    st.image(str(path), width="stretch")
                    bcols = st.columns([4, 1])
                    with bcols[0]:
                        if st.button(
                            f"🔍 #{seq} · {path.name}",
                            key=f"preview_btn_{seq}",
                            width="stretch",
                            help="Click to enlarge to original size",
                        ):
                            _preview_dialog(seq, path)
                    with bcols[1]:
                        regen_help = "Regenerate only this output (1 call)."
                        if seq == 7:
                            regen_help += (
                                " ⚠️ #7 is the basis for #4·5·6·11 — "
                                "after regenerating #7, regenerating those four is recommended."
                            )
                        if st.button(
                            "🔄",
                            key=f"regen_btn_{seq}",
                            width="stretch",
                            help=regen_help,
                        ):
                            with st.spinner(f"Regenerating #{seq}…"):
                                try:
                                    pipeline.regenerate(
                                        out_dir, seq,
                                        model_id=model_id,
                                        prompt_overrides=prompt_overrides,
                                        style_notes=style_notes,
                                    )
                                    st.rerun()
                                except Exception as e:  # noqa: BLE001
                                    st.error(f"Failed to regenerate #{seq}: {e}")
                    st.write("")  # spacer
                else:
                    st.warning(f"#{seq} file not found")
    else:
        st.warning("No outputs were generated.")

    # Failures
    failures = meta.get("failures", []) or []
    if failures:
        with st.expander(f"{len(failures)} failed step(s)"):
            for f in failures:
                st.write(f"- **{f.get('step')}**: {f.get('error')}")

    # Cost + folder + ZIP
    cost = meta.get("stats", {}).get("estimated_cost_usd")
    duration = meta.get("stats", {}).get("duration_sec")
    info_bits: list[str] = []
    if cost is not None:
        info_bits.append(f"Cost: ${cost:.4f}")
    if duration is not None:
        info_bits.append(f"Elapsed: {duration:.1f}s")
    if info_bits:
        st.info(" · ".join(info_bits))

    if outputs:
        folder = next(iter(outputs.values())).parent.resolve()
        st.caption(f"Local folder: `{folder}`")
        try:
            zip_bytes = build_zip_bytes(result)
            st.download_button(
                "Download ZIP",
                data=zip_bytes,
                file_name=f"{slug or 'poster_outputs'}.zip",
                mime="application/zip",
            )
        except Exception as e:  # noqa: BLE001
            st.warning(f"Failed to create ZIP: {e}")


# ---------------------------------------------------------------------------
# Access gate
# ---------------------------------------------------------------------------


def _check_password() -> bool:
    """Gate the app behind a shared password.

    The expected password comes from the ``APP_PASSWORD`` env var (set via
    Streamlit secrets in the cloud, or ``.env`` locally). If unset, the gate is
    disabled and the app is open — so local dev keeps working without config.
    """
    expected = os.environ.get("APP_PASSWORD")
    if not expected:
        return True  # no password configured → open access

    if st.session_state.get("_authed"):
        return True

    st.title("🔒 " + APP_TITLE)
    entered = st.text_input("Password", type="password", key="_pw_input")
    if st.button("Enter", key="_pw_submit"):
        if entered == expected:
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    init_session_state()

    if not _check_password():
        st.stop()

    cfg = load_config()
    models, default_idx = get_model_options(cfg)

    # ---- API key guard ---------------------------------------------------
    if not os.environ.get("OPENROUTER_API_KEY"):
        st.error(
            "Environment variable `OPENROUTER_API_KEY` is not set. "
            f"Create a `.env` file using `.env.example` as a reference. "
            f"(path: `{ENV_EXAMPLE_PATH}`)"
        )
        st.stop()

    # ---- Sidebar ---------------------------------------------------------
    with st.sidebar:
        st.header("AI Model")
        labels = [m["label"] for m in models]
        selection = st.selectbox(
            "Image generation model",
            options=list(range(len(models))),
            format_func=lambda i: labels[i],
            index=default_idx,
        )
        selected_model = models[selection]
        per_image = selected_model["cost_per_image"]
        # 12 image calls per title (PRD Appendix B).
        est_total = per_image * 12
        st.caption(
            f"${per_image:.3f} per image · ~${est_total:.2f} per title (12 calls)"
        )
        st.caption(f"Model ID: `{selected_model['id']}`")

    # ---- Main ------------------------------------------------------------
    st.title(APP_TITLE)

    uploaded = st.file_uploader(
        "Upload source poster",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=False,
    )
    if uploaded is not None:
        new_bytes = uploaded.getvalue()
        if new_bytes != st.session_state.uploaded_bytes:
            # New source image → any pending clean preview is stale.
            st.session_state.clean_preview = None
            st.session_state.clean_source = None
        st.session_state.uploaded_bytes = new_bytes
        st.session_state.uploaded_name = uploaded.name

    if st.session_state.uploaded_bytes:
        st.caption(
            f"Uploaded file: **{st.session_state.uploaded_name}** "
            f"({len(st.session_state.uploaded_bytes) / 1024:.0f} KB)"
        )
        st.image(
            st.session_state.uploaded_bytes,
            caption="Source portrait",
            width=240,
        )

    # ---- Title fields ----------------------------------------------------
    # If the user clicked "Fill the rest with AI" on the previous run, fulfill it
    # NOW — before the text_input widgets claim those session_state keys.
    # (Streamlit forbids writing to a key after a widget with that key exists.)
    if st.session_state._pending_fill:
        st.session_state._pending_fill = False
        fill_missing_titles()

    st.text_input(LANG_LABELS["kr"], key="title_kr")
    st.text_input(LANG_LABELS["en"], key="title_en")
    st.text_input(LANG_LABELS["zh"], key="title_zh")

    st.text_input(
        "Style notes (optional)",
        key="style_notes",
        placeholder="e.g. keep gold-foil texture / make English title slightly smaller / keep brush-stroke feel",
        help="Added as designer instructions to the title compose/extract prompts.",
    )

    # ---- Prompt editor (advanced) ---------------------------------------
    # Two-phase reset: if a reset was requested on the previous run, apply it
    # NOW before the text_area widgets claim their session_state keys.
    for step_key, _label, filename in PROMPT_STEPS:
        flag_key = f"_pending_reset_{step_key}"
        if st.session_state.get(flag_key):
            st.session_state[flag_key] = False
            st.session_state[f"prompt_{step_key}"] = _load_prompt_default(filename)

    with st.expander("Edit prompts (advanced) — 5 steps", expanded=False):
        st.caption(
            "You can temporarily override each step's prompt for this run only. "
            "Leave blank to use the defaults from the prompts/ folder. "
            "The title compose/extract prompts must include the `{title}` and "
            "`{language}` placeholders."
        )
        for step_key, label, filename in PROMPT_STEPS:
            cols = st.columns([6, 1])
            with cols[0]:
                st.text_area(
                    label,
                    key=f"prompt_{step_key}",
                    height=140,
                    disabled=st.session_state.is_running,
                )
            with cols[1]:
                if st.button("Reset", key=f"reset_{step_key}",
                             disabled=st.session_state.is_running,
                             width="stretch"):
                    st.session_state[f"_pending_reset_{step_key}"] = True
                    st.rerun()

    # ---- Action buttons --------------------------------------------------
    titles_filled = all(
        st.session_state[f"title_{l}"].strip() for l in LANGS
    )
    upload_ready = st.session_state.uploaded_bytes is not None
    can_generate = titles_filled and upload_ready and not st.session_state.is_running

    # Collect prompt overrides — only non-default values get passed.
    overrides: dict[str, str] = {}
    for step_key, _label, filename in PROMPT_STEPS:
        edited = (st.session_state.get(f"prompt_{step_key}") or "").strip()
        default = _load_prompt_default(filename).strip()
        if edited and edited != default:
            overrides[step_key] = edited

    per_image = selected_model["cost_per_image"]

    col_fill, col_clean, col_run = st.columns(3)
    with col_fill:
        if st.button(
            "✨ Fill the rest with AI",
            width="stretch",
            disabled=st.session_state.is_running,
        ):
            st.session_state._pending_fill = True
            st.rerun()

    with col_clean:
        clean_clicked = st.button(
            "1️⃣ Preview text removal",
            width="stretch",
            disabled=not (upload_ready and not st.session_state.is_running),
            help=f"Run only step 1 (text removal) to review the result (~${per_image:.2f}).",
        )

    with col_run:
        run_clicked = st.button(
            "Generate all (skip review)",
            type="primary",
            width="stretch",
            disabled=not can_generate,
            help=f"Run all 12 calls at once (~${per_image * 12:.2f}).",
        )

    if not upload_ready:
        st.caption("⚠️ Please upload a source poster.")
    elif not titles_filled:
        st.caption("⚠️ All three language titles must be filled in to generate.")

    # ---- Stage 1: clean preview (run + review) ----------------------------
    approve_clicked = False
    if clean_clicked:
        st.session_state.result = None
        with st.spinner("Step 1: removing text…"):
            try:
                st.session_state.clean_preview = pipeline.stage_clean(
                    st.session_state.uploaded_bytes,
                    model_id=selected_model["id"],
                    prompt_override=overrides.get("clean"),
                )
                st.session_state.clean_source = "ai"
            except Exception as e:  # noqa: BLE001
                st.error(f"Text removal failed: {e}")

    if st.session_state.clean_preview is not None:
        st.divider()
        st.subheader("Step 1 review — text removal result")
        src_label = "AI-generated" if st.session_state.clean_source == "ai" else "uploaded"
        st.caption(
            f"({src_label}) Check that the body text was cleanly removed and that "
            "the artwork is undamaged. This image is the basis for the landscape and banner outputs."
        )
        st.image(st.session_state.clean_preview, width=300)

        col_retry, col_approve = st.columns(2)
        with col_retry:
            if st.button(
                "🔄 Remove again",
                width="stretch",
                disabled=st.session_state.is_running,
                help=f"Run step 1 again (~${per_image:.2f}).",
            ):
                with st.spinner("Step 1: retrying text removal…"):
                    try:
                        st.session_state.clean_preview = pipeline.stage_clean(
                            st.session_state.uploaded_bytes,
                            model_id=selected_model["id"],
                            prompt_override=overrides.get("clean"),
                        )
                        st.session_state.clean_source = "ai"
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Text removal failed: {e}")
        with col_approve:
            approve_clicked = st.button(
                "✅ Approve and generate the rest",
                type="primary",
                width="stretch",
                disabled=not can_generate,
                help=f"Run the remaining 11 calls using the approved clean base (~${per_image * 11:.2f}).",
            )
        if not can_generate and upload_ready:
            st.caption("⚠️ All three language titles must be filled in to continue.")

    # Designer-provided clean base (skips the AI clean step entirely).
    with st.expander("Upload your own clean base (skip step 1)", expanded=False):
        st.caption(
            "If you have a portrait poster with the text already removed (e.g. in "
            "Photoshop), upload it here. The pipeline will use it as the base without AI text removal."
        )
        manual_clean = st.file_uploader(
            "Clean poster (portrait, no text)",
            type=["jpg", "jpeg", "png"],
            key="manual_clean_upload",
        )
        if manual_clean is not None:
            mc_bytes = manual_clean.getvalue()
            if mc_bytes != st.session_state.clean_preview:
                st.session_state.clean_preview = mc_bytes
                st.session_state.clean_source = "upload"
                st.rerun()

    # ---- Run -------------------------------------------------------------
    if (run_clicked or approve_clicked) and can_generate:
        st.session_state.is_running = True
        st.session_state.result = None

        precleaned = st.session_state.clean_preview if approve_clicked else None
        titles = {l: st.session_state[f"title_{l}"].strip() for l in LANGS}
        image_path = save_upload_to_disk(
            st.session_state.uploaded_name or "upload.jpg",
            st.session_state.uploaded_bytes,
        )

        st.divider()
        st.subheader("Progress")
        progress_bar = st.progress(0.0)
        status_box = st.empty()
        status_box.write("0/11 — starting…")

        try:
            result = run_pipeline(
                image_path,
                titles,
                selected_model["id"],
                progress_bar,
                status_box,
                prompt_overrides=overrides or None,
                precleaned=precleaned,
                style_notes=st.session_state.style_notes.strip() or None,
            )
            st.session_state.result = result
        finally:
            st.session_state.is_running = False

        if st.session_state.result is not None:
            status_box.write("Done")
            progress_bar.progress(1.0)
            # The clean preview was consumed by this run.
            st.session_state.clean_preview = None
            st.session_state.clean_source = None

    # ---- Results ---------------------------------------------------------
    if st.session_state.result is not None:
        st.divider()
        render_results(
            st.session_state.result,
            model_id=selected_model["id"],
            prompt_overrides=overrides or None,
            style_notes=st.session_state.style_notes.strip() or None,
        )


if __name__ == "__main__":
    main()
