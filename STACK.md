# Tech Stack — OTT Poster Automation System v1

A complete reference for the technologies, dependencies, external services, and runtime architecture behind the poster automation tool. For how the modules fit together see [ARCHITECTURE.md](./ARCHITECTURE.md).

---

## 1. At a glance

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| UI framework | Streamlit (1.30 – <2.0) |
| Image generation | Image models via OpenRouter API (default: Gemini Flash Image family) |
| Text/translation | Text LLM via OpenRouter (default: `google/gemini-2.5-flash`) |
| Image processing | Pillow (PIL) 10.2 – <12.0 |
| HTTP client | requests 2.31 – <3.0 |
| Config | PyYAML 6.0 (`config.yaml`) |
| Secrets (local) | python-dotenv 1.0 (`.env`) |
| Secrets (cloud) | Streamlit secrets (`st.secrets`, TOML) |
| Hosting | Streamlit Community Cloud |
| Source control | Git / GitHub |
| Testing | pytest (all network calls mocked) |

---

## 2. Runtime & language

- **Python 3.11**, pinned for Streamlit Community Cloud via `runtime.txt` (`python-3.11`).
- Pure-Python application; no compiled extensions of our own. Pillow ships prebuilt wheels.
- Local development uses a `.venv` virtualenv.

---

## 3. Dependencies (`requirements.txt`)

| Package | Version range | Role |
|---|---|---|
| `streamlit` | `>=1.30.0,<2.0.0` | Web UI, session state, widgets, file upload, `@st.dialog`, `@st.cache_data`, secrets |
| `Pillow` | `>=10.2.0,<12.0.0` | Pixel snapping (Lanczos resize / center-crop), chroma keying, PNG/RGBA handling, upscale-on-download |
| `requests` | `>=2.31.0,<3.0.0` | HTTP calls to OpenRouter and Google AI Studio APIs |
| `pyyaml` | `>=6.0.1,<7.0.0` | Parse `config.yaml` |
| `python-dotenv` | `>=1.0.0,<2.0.0` | Load `.env` into environment for local dev |

`pytest` is a dev-only dependency (not in `requirements.txt`); install separately to run the suite.

---

## 4. External services

### OpenRouter (primary backend)
- Endpoint: `https://openrouter.ai/api/v1/chat/completions`
- Used for **both** image generation (`gemini.py`) and text translation/detection (`translate.py`).
- Auth: `OPENROUTER_API_KEY` (Bearer token).
- Constraint: **30MB input limit** per request.
- Image model catalog is defined in `config.yaml::models.available`; only image-output-capable models verified on OpenRouter are listed. Models over $0.10/image are disabled by default for cost control.

### Google AI Studio (optional, high-resolution backend)
- Activates automatically when `GOOGLE_API_KEY` is set.
- For inputs larger than 18MB, uploads via the **Google Files API** first (bypasses the OpenRouter 30MB limit).
- Falls back to OpenRouter when the key is absent.

### Default models
| Purpose | Default model | Est. cost |
|---|---|---|
| Image (default) | `google/gemini-3.1-flash-image-preview` | ~$0.05 / image |
| Image (cheapest) | `google/gemini-2.5-flash-image` (Nano Banana) | ~$0.04 / image |
| Image (alt) | `openai/gpt-5-image-mini` | ~$0.04 / image |
| Text LLM | `google/gemini-2.5-flash` | ~$0.001 / call |

> Note: FLUX / Seedream / Ideogram are not on OpenRouter — they would require direct fal.ai / Replicate integration.

---

## 5. Application modules

| Module | Responsibility | Key tech |
|---|---|---|
| `app.py` | Streamlit UI: password gate, secrets bridge, sidebar, staged review, result grid, regeneration | streamlit |
| `pipeline.py` | 11-output orchestration (`process`), `stage_clean`, `regenerate`; `_meta.json` + `_work/` bookkeeping | stdlib, ThreadPoolExecutor |
| `gemini.py` | Image-model wrapper (OpenRouter / Google direct), retries, prompt formatting, style-note injection | requests |
| `translate.py` | Language detection + localization-aware translation | requests |
| `dims.py` | Exact pixel snapping + magenta chroma keying with border-color fallback | Pillow |

See ARCHITECTURE.md §3 for full signatures.

---

## 6. Image-processing pipeline (Pillow)

- **Pixel snapping** (`dims.snap`): `crop` mode center-crops to the target ratio then Lanczos-resizes; `resize` mode resizes directly (for alpha PNG logos). Guarantees exact output dimensions.
- **Chroma keying** (`dims.chroma_key_magenta`): converts a `#FF00FF` magenta background to transparency using Chebyshev color distance, with a soft alpha ramp (tolerance → 2×tolerance) to reduce edge fringing. `fallback_auto` detects the median border color and re-keys when the model drifts the background toward pink.
- **Upscale-on-download**: the preview dialog offers x2/x4 Lanczos upscales, computed lazily and cached via `@st.cache_data`. This is resampling, not AI super-resolution.

Output pixel specs: portrait 900×1600, landscape 1600×900, title logo 580×200 (transparent), banner 1520×536.

---

## 7. Configuration & secrets

| Mechanism | Local | Cloud |
|---|---|---|
| App config | `config.yaml` (committed) | `config.yaml` (committed) |
| Secrets | `.env` (gitignored) | Streamlit Secrets (TOML) |

- `app.py` bridges `st.secrets` → `os.environ` at startup so all modules read environment variables uniformly, regardless of where they were set.
- Recognized secrets: `OPENROUTER_API_KEY` (required), `GOOGLE_API_KEY` (optional), `APP_PASSWORD` (optional shared-access gate).
- `config.yaml` controls the model catalog, text LLM, filename template, output folder pattern, pixel specs, and per-model prompt overrides.
- Prompts live as plain-text templates in `prompts/*.txt` (the source of truth), overridable per-run from the UI or per-model via config.

---

## 8. Hosting & deployment

- **Streamlit Community Cloud**, deployed from a GitHub repo at `share.streamlit.io`.
  - Main file: `app.py`; branch: `main`.
  - Python pinned by `runtime.txt`.
  - Free tier: 1 private app, ~1GB memory, **ephemeral disk** — generated `ready/` outputs do not persist across restarts, so users download a ZIP or individual files.
- **Local**: `streamlit run app.py` after installing requirements in a virtualenv.

---

## 9. Testing

- **pytest** suite under `tests/`, with all OpenRouter/Google network calls mocked — runs with no API key and no cost.
- Coverage includes: language detection/translation, prompt rendering, the 12-call pipeline contract, `precleaned` skip behavior, `_work/` persistence, style-note passthrough, per-output `regenerate`, chroma-key magenta + pink-drift fallback, and `_meta.json` schema completeness.
- Current status: 72 tests passing.

---

## 10. Cost & performance characteristics

- **~$0.60 per title** at the default model: 12 image calls × ~$0.05 + 3 text calls × ~$0.001.
- Staged review (preview clean → approve) skips one image call and lets designers catch failures before committing the remaining 11 — reducing wasted spend.
- Per-output regeneration costs ~$0.05 (a single call) and reuses persisted `_work/` intermediates.
- All PIL operations (chroma keying, pixel snapping, upscaling) run locally and are free.
- Meets the PRD KR5 target of under $1 per title.

---

## 11. Notable design decisions

- **OpenRouter as a single gateway** for both image and text models keeps auth and billing in one place, with a per-model catalog swappable via config.
- **Generate-large-then-snap**: models are asked for larger-than-target images, then PIL snaps to exact pixels — decouples model output size from delivery spec.
- **Magenta chroma key over model-native transparency**: image models render transparency unreliably, so logos are generated on `#FF00FF` and keyed locally, with border-color fallback for color drift.
- **Two-phase Streamlit widget pattern**: because Streamlit forbids writing a session-state key after its widget exists, "fill titles" and "reset prompt" set a flag and `st.rerun()`, applying the change before the widget is created.
- **`_work/` intermediates + `_meta.json`** make single-output regeneration possible without re-running the whole pipeline.
