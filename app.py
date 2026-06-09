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

APP_TITLE = "포스터 자동화 v1"
CONFIG_PATH = Path(__file__).parent / "config.yaml"
INPUTS_DIR = Path(__file__).parent / "inputs"
PROMPTS_DIR = Path(__file__).parent / "prompts"
ENV_EXAMPLE_PATH = Path(__file__).parent / ".env.example"

PROMPT_STEPS: list[tuple[str, str, str]] = [
    # (step_key, label, prompt_file)
    ("clean",              "1. 본문 텍스트 제거 (clean)",              "clean.txt"),
    ("outpaint_landscape", "2. Portrait → Landscape 변환",            "outpaint_landscape.txt"),
    ("outpaint_banner",    "3. Landscape → CN Banner 변환 (텍스트X)", "outpaint_banner.txt"),
    ("title_swap",         "4. 타이틀 합성/교체 (placeholders: {title}, {language})", "title_swap.txt"),
    ("title_extract",      "5. 타이틀 로고 추출 (magenta 배경)",       "title_extract.txt"),
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
        st.warning("최소 1개 타이틀을 입력하세요.")
        return

    src_lang, src_value = filled[0]
    targets = [l for l in LANGS if l != src_lang and not current[l]]
    if not targets:
        st.info("모든 타이틀이 이미 채워져 있습니다.")
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
        st.warning(f"자동 번역 실패: {e} — 수동 입력하세요.")
        return

    for lang in targets:
        if lang in translated and translated[lang]:
            st.session_state[f"title_{lang}"] = translated[lang]
    st.success(f"{', '.join(targets).upper()} 타이틀을 자동 입력했습니다.")


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
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"파이프라인 실행 중 오류가 발생했습니다: {e}")
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


@st.dialog("미리보기", width="large")
def _preview_dialog(seq: int, path: Path) -> None:
    """Full-size image popup. Press Esc or click outside to close (native)."""
    st.caption(f"#{seq} — {path.name}")
    if not path.exists():
        st.warning(f"파일을 찾을 수 없음: {path}")
        return

    st.image(str(path), width="stretch")

    try:
        from PIL import Image as PILImage
        with PILImage.open(path) as _im:
            w, h = _im.size
        st.caption(
            f"원본 {w}×{h} · x2 → {w*2}×{h*2} · x4 → {w*4}×{h*4}  "
            f"(Lanczos 리샘플링, AI 아님)"
        )
    except Exception:
        pass

    mtime = path.stat().st_mtime
    stem, suffix = path.stem, ".png"

    col1, col2, col3 = st.columns(3)
    with col1:
        st.download_button(
            "원본 다운로드",
            data=path.read_bytes(),
            file_name=path.name,
            mime="image/png",
            width="stretch",
            key=f"dl_orig_{seq}",
        )
    with col2:
        st.download_button(
            "x2 업스케일",
            data=_upscale_bytes(str(path), mtime, 2),
            file_name=f"{stem}@2x{suffix}",
            mime="image/png",
            width="stretch",
            key=f"dl_2x_{seq}",
        )
    with col3:
        st.download_button(
            "x4 업스케일",
            data=_upscale_bytes(str(path), mtime, 4),
            file_name=f"{stem}@4x{suffix}",
            mime="image/png",
            width="stretch",
            key=f"dl_4x_{seq}",
        )


def render_results(result: dict[str, Any]) -> None:
    """Render thumbnail grid, cost summary, ZIP download, and folder hint."""
    outputs: dict[int, Path] = result.get("outputs", {})
    meta: dict[str, Any] = result.get("meta", {})
    slug = result.get("slug", "")

    st.subheader("결과")
    produced = len(outputs)
    expected = meta.get("stats", {}).get("outputs_expected", 11)
    st.caption(f"{produced}/{expected} 출력 생성됨 — 파일명 버튼 클릭 시 확대 (ESC로 닫기)")

    if outputs:
        cols = st.columns(4)
        for idx, seq in enumerate(sorted(outputs)):
            path = outputs[seq]
            with cols[idx % 4]:
                if path.exists():
                    st.image(str(path), width="stretch")
                    if st.button(
                        f"🔍 #{seq} · {path.name}",
                        key=f"preview_btn_{seq}",
                        width="stretch",
                        help="클릭하여 원본 크기로 확대",
                    ):
                        _preview_dialog(seq, path)
                    st.write("")  # spacer
                else:
                    st.warning(f"#{seq} 파일을 찾을 수 없음")
    else:
        st.warning("생성된 출력이 없습니다.")

    # Failures
    failures = meta.get("failures", []) or []
    if failures:
        with st.expander(f"실패 단계 {len(failures)}건"):
            for f in failures:
                st.write(f"- **{f.get('step')}**: {f.get('error')}")

    # Cost + folder + ZIP
    cost = meta.get("stats", {}).get("estimated_cost_usd")
    duration = meta.get("stats", {}).get("duration_sec")
    info_bits: list[str] = []
    if cost is not None:
        info_bits.append(f"비용: ${cost:.4f}")
    if duration is not None:
        info_bits.append(f"소요: {duration:.1f}s")
    if info_bits:
        st.info(" · ".join(info_bits))

    if outputs:
        folder = next(iter(outputs.values())).parent.resolve()
        st.caption(f"로컬 폴더: `{folder}`")
        try:
            zip_bytes = build_zip_bytes(result)
            st.download_button(
                "ZIP 다운로드",
                data=zip_bytes,
                file_name=f"{slug or 'poster_outputs'}.zip",
                mime="application/zip",
            )
        except Exception as e:  # noqa: BLE001
            st.warning(f"ZIP 생성 실패: {e}")


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
    entered = st.text_input("비밀번호", type="password", key="_pw_input")
    if st.button("입장", key="_pw_submit"):
        if entered == expected:
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
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
            "환경 변수 `OPENROUTER_API_KEY`가 설정되지 않았습니다. "
            f"`.env.example` 파일을 참고하여 `.env`를 만들어주세요. "
            f"(경로: `{ENV_EXAMPLE_PATH}`)"
        )
        st.stop()

    # ---- Sidebar ---------------------------------------------------------
    with st.sidebar:
        st.header("AI 모델")
        labels = [m["label"] for m in models]
        selection = st.selectbox(
            "이미지 생성 모델",
            options=list(range(len(models))),
            format_func=lambda i: labels[i],
            index=default_idx,
        )
        selected_model = models[selection]
        per_image = selected_model["cost_per_image"]
        # 12 image calls per title (PRD 부록 B).
        est_total = per_image * 12
        st.caption(
            f"이미지당 ${per_image:.3f} · 타이틀당 약 ${est_total:.2f} (12회 호출)"
        )
        st.caption(f"모델 ID: `{selected_model['id']}`")

    # ---- Main ------------------------------------------------------------
    st.title(APP_TITLE)

    uploaded = st.file_uploader(
        "원본 포스터 업로드",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=False,
    )
    if uploaded is not None:
        st.session_state.uploaded_bytes = uploaded.getvalue()
        st.session_state.uploaded_name = uploaded.name

    if st.session_state.uploaded_bytes:
        st.caption(
            f"업로드된 파일: **{st.session_state.uploaded_name}** "
            f"({len(st.session_state.uploaded_bytes) / 1024:.0f} KB)"
        )
        st.image(
            st.session_state.uploaded_bytes,
            caption="원본 portrait",
            width=240,
        )

    # ---- Title fields ----------------------------------------------------
    # If the user clicked "AI로 나머지 채우기" on the previous run, fulfill it
    # NOW — before the text_input widgets claim those session_state keys.
    # (Streamlit forbids writing to a key after a widget with that key exists.)
    if st.session_state._pending_fill:
        st.session_state._pending_fill = False
        fill_missing_titles()

    st.text_input(LANG_LABELS["kr"], key="title_kr")
    st.text_input(LANG_LABELS["en"], key="title_en")
    st.text_input(LANG_LABELS["zh"], key="title_zh")

    # ---- Prompt editor (advanced) ---------------------------------------
    # Two-phase reset: if a reset was requested on the previous run, apply it
    # NOW before the text_area widgets claim their session_state keys.
    for step_key, _label, filename in PROMPT_STEPS:
        flag_key = f"_pending_reset_{step_key}"
        if st.session_state.get(flag_key):
            st.session_state[flag_key] = False
            st.session_state[f"prompt_{step_key}"] = _load_prompt_default(filename)

    with st.expander("프롬프트 편집 (고급) — 5단계", expanded=False):
        st.caption(
            "각 단계의 프롬프트를 이 실행에만 임시로 덮어쓸 수 있습니다. "
            "비워두면 prompts/ 폴더의 기본값이 사용됩니다. "
            "타이틀 합성·추출 프롬프트는 `{title}`, `{language}` 플레이스홀더를 "
            "포함해야 합니다."
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
                if st.button("초기화", key=f"reset_{step_key}",
                             disabled=st.session_state.is_running,
                             width="stretch"):
                    st.session_state[f"_pending_reset_{step_key}"] = True
                    st.rerun()

    # ---- Action buttons --------------------------------------------------
    col_fill, col_run = st.columns(2)
    with col_fill:
        if st.button(
            "✨ AI로 나머지 채우기",
            width="stretch",
            disabled=st.session_state.is_running,
        ):
            st.session_state._pending_fill = True
            st.rerun()

    titles_filled = all(
        st.session_state[f"title_{l}"].strip() for l in LANGS
    )
    upload_ready = st.session_state.uploaded_bytes is not None
    can_generate = titles_filled and upload_ready and not st.session_state.is_running

    with col_run:
        run_clicked = st.button(
            "생성 시작",
            type="primary",
            width="stretch",
            disabled=not can_generate,
        )

    if not upload_ready:
        st.caption("⚠️ 원본 포스터를 업로드하세요.")
    elif not titles_filled:
        st.caption("⚠️ 3개 언어 타이틀이 모두 채워져야 생성할 수 있습니다.")

    # ---- Run -------------------------------------------------------------
    if run_clicked and can_generate:
        st.session_state.is_running = True
        st.session_state.result = None

        titles = {l: st.session_state[f"title_{l}"].strip() for l in LANGS}
        image_path = save_upload_to_disk(
            st.session_state.uploaded_name or "upload.jpg",
            st.session_state.uploaded_bytes,
        )

        st.divider()
        st.subheader("진행 상황")
        progress_bar = st.progress(0.0)
        status_box = st.empty()
        status_box.write("0/11 — 시작 중…")

        # Collect prompt overrides — only non-empty values get passed.
        overrides: dict[str, str] = {}
        for step_key, _label, filename in PROMPT_STEPS:
            edited = (st.session_state.get(f"prompt_{step_key}") or "").strip()
            default = _load_prompt_default(filename).strip()
            if edited and edited != default:
                overrides[step_key] = edited

        try:
            result = run_pipeline(
                image_path,
                titles,
                selected_model["id"],
                progress_bar,
                status_box,
                prompt_overrides=overrides or None,
            )
            st.session_state.result = result
        finally:
            st.session_state.is_running = False

        if st.session_state.result is not None:
            status_box.write("완료")
            progress_bar.progress(1.0)

    # ---- Results ---------------------------------------------------------
    if st.session_state.result is not None:
        st.divider()
        render_results(st.session_state.result)


if __name__ == "__main__":
    main()
