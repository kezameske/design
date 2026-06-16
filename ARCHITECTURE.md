# Architecture — OTT Poster Automation System v1

PRD: [PRD-poster-automation.md](./PRD-poster-automation.md)

This is the developer-facing technical design document. It covers the file structure, data flow, module responsibilities, Gemini prompt strategy, and error-handling approach.

---

## 1. File structure

```
design/
├── PRD-poster-automation.md     # PRD (requirements snapshot)
├── ARCHITECTURE.md              # this document
├── WORKFLOW.md                  # designer-facing usage guide
├── README.md                    # install / run / deploy guide
├── STACK.md                     # full tech-stack reference
├── requirements.txt             # dependencies
├── runtime.txt                  # Streamlit Cloud Python version pin
├── .env.example                 # OPENROUTER_API_KEY / GOOGLE_API_KEY / APP_PASSWORD examples
├── config.yaml                  # model catalog, filename template, specs, prompt overrides
│
├── app.py                       # Streamlit UI (password gate, staged review, regeneration)
├── pipeline.py                  # 11-output orchestration + stage_clean + regenerate
├── gemini.py                    # image API wrapper (OpenRouter / Google direct)
├── translate.py                 # language detection + translation
├── dims.py                      # PIL pixel snapping + chroma-key utilities
│
├── prompts/                     # prompt templates (source of truth)
│   ├── clean.txt
│   ├── outpaint_landscape.txt
│   ├── outpaint_banner.txt
│   ├── title_swap.txt
│   └── title_extract.txt
│
├── tests/                       # pytest (all API calls mocked)
├── inputs/                      # temporary storage for user uploads
└── ready/
    └── <slug>/                  # output folder per title (11 files)
        ├── kr-portrait-title.png      # 900x1600   (#1)
        ├── en-portrait-title.png      #            (#2)
        ├── cn-portrait-title.png      #            (#3)
        ├── kr-landscape-title.png     # 1600x900   (#4)
        ├── en-landscape-title.png     #            (#5)
        ├── cn-landscape-title.png     #            (#6)
        ├── clean-landscape-title.png  # no text    (#7)
        ├── kr-logo-title.png          # 580x200 transparent (#8)
        ├── en-logo-title.png          #            (#9)
        ├── cn-logo-title.png          #            (#10)
        ├── cn-main-banner-title.png   # 1520x536, no text (#11)
        ├── _meta.json
        └── _work/                     # intermediate artifacts for regeneration
            ├── source.png             # uploaded original
            └── clean_portrait.png     # STEP B result
```

---

## 2. Data flow (per title)

```
[input]
  portrait.jpg + title string (1 of KR/EN/CN)
        │
        ▼
┌─────────────────────────────────────────────────┐
│ STEP A. Language detection + translation          │
│   translate.detect(title) → src_lang             │
│   translate.translate(title, src_lang, [others]) │
│     → {kr: "...", en: "...", zh: "..."}          │
└─────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────┐
│ STEP B. Generate clean_portrait (1 call)          │
│   gemini.clean(portrait) → clean_portrait        │
│   ※ base with both body text and title removed    │
└─────────────────────────────────────────────────┘
        │
        ├─────────────────────────────┐
        ▼                             ▼
┌──────────────────────┐   ┌──────────────────────┐
│ STEP C. Portrait      │   │ STEP D. Generate      │
│ title swap × 3        │   │ clean_landscape       │
│  for lang in {kr,en,  │   │  gemini.outpaint(    │
│    zh}:               │   │    clean_portrait,   │
│    gemini.title_swap( │   │    "landscape")      │
│      clean_portrait,  │   │  → output #7 (clean) │
│      titles[lang])    │   │                      │
│  → outputs #1, #2, #3 │   │                      │
└──────────────────────┘   └──────────────────────┘
        │                              │
        │                   ┌──────────┴──────────┐
        │                   ▼                     ▼
        │         ┌──────────────────┐  ┌──────────────────┐
        │         │ STEP E. Landscape │  │ STEP F. CN banner │
        │         │ title swap × 3    │  │ outpaint          │
        │         │  → outputs        │  │  gemini.outpaint( │
        │         │  #4, #5, #6       │  │    clean_         │
        │         └──────────────────┘  │    landscape,     │
        │                               │    "banner")      │
        │                               │  → output #11     │
        │                               │  (no title swap)  │
        │                               └──────────────────┘
        ▼
┌─────────────────────────────────────────────────┐
│ STEP G. Title logo extraction × 3                 │
│   for lang in {kr, en, zh}:                      │
│     raw = gemini.title_extract(                  │
│             portraits[lang], ref=source)         │
│     logo = dims.chroma_key_magenta(              │
│             raw, fallback_auto=True)             │
│       ※ #FF00FF background → transparent. Even   │
│         if the model drifts toward pink, the     │
│         border color is auto-detected for keying │
│   → outputs #8, #9, #10                          │
└─────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────┐
│ STEP I. Pixel snap (all 11 outputs)               │
│   for each output:                               │
│     dims.snap(image, target_dim)                 │
│   → guarantees exact pixel dimensions            │
└─────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────┐
│ STEP J. Save + write _meta.json + _work/          │
└─────────────────────────────────────────────────┘
```

(The banner is output **#11** from STEP F; the white-logo variant is excluded in v1 — 11 outputs total.)

**Total Gemini calls:** 1 (clean) + 3 (portrait swap) + 1 (landscape outpaint) + 3 (landscape swap) + 1 (banner outpaint) + 3 (title extract) = **12**
(In the staged-review flow, passing the approved clean base as `precleaned` skips STEP B → 11 calls.)

**Total LLM calls:** 1 (detect) + 2 (translate) = **3**

**Total PIL operations:** 3 (chroma key) + 11 (pixel snap) = 14 (all local, free)

### Staged execution entry points (v1.5)

```
stage_clean(source)            # STEP B only — UI's "Preview text removal"
process(..., precleaned=...)   # skip STEP B, run C–J — "Approve and generate the rest"
regenerate(out_dir, seq)       # re-run a single output — 🔄 button in the result grid
```

`regenerate()` uses the intermediate artifacts in `_work/` and the upstream output files on disk as its sources:

| seq | source | API call |
|---|---|---|
| 1–3 | `_work/source.png` | title_swap |
| 7 | `_work/clean_portrait.png` | outpaint(landscape) |
| 4–6 | output #7 file (+ source reference) | title_swap |
| 11 | output #7 file | outpaint(banner) |
| 8–10 | output #1/2/3 files (+ source reference) | title_extract + chroma key |

※ Regenerating #7 does not automatically propagate to #4·5·6·11 — the UI shows a recommendation to regenerate them.

---

## 3. Module responsibilities

### `gemini.py`

Image-model call wrapper. The default backend is OpenRouter; if `GOOGLE_API_KEY` is set it automatically switches to direct Google AI Studio calls (inputs over 18MB go through the Files API).

```python
def clean(image_bytes, *, model_id=None, prompt_override=None) -> bytes:
    """Remove all text (including the title) from the poster. Returns a clean base."""

def outpaint(image_bytes, target: Literal["landscape", "banner"], *,
             model_id=None, prompt_override=None) -> bytes:
    """Aspect-ratio conversion. landscape=16:9, banner=1520:536 outpainting."""

def title_swap(clean_base, title, lang, *, model_id=None, prompt_override=None,
               reference_bytes=None, style_notes=None) -> bytes:
    """Swap/compose the title. reference_bytes carries the original typography
    reference; style_notes appends designer instructions."""

def title_extract(poster, title, lang, *, model_id=None, prompt_override=None,
                  reference_bytes=None, style_notes=None) -> bytes:
    """Isolate just the title onto a #FF00FF magenta background (chroma-keyed downstream)."""
```

**Key implementation details:**
- Every function **requests a larger-than-target size**, then dims.py snaps to exact pixels.
- Automatic retry ×3 on call failure (exponential backoff).
- Response-parse failures (no image returned) are also retried.
- `style_notes` is appended as a "Designer notes" section at the end of the formatted prompt.

### `translate.py`

Text LLM via OpenRouter (`config.yaml::text_llm`, default `google/gemini-2.5-flash`).

```python
def detect(title: str) -> Literal["kr", "en", "zh"]:
    """Guess the language from the title string."""

def translate(title: str, src: str, targets: list[str]) -> dict[str, str]:
    """Translate from src language into each target. Returns {lang: translated}."""
```

**Prompt strategy:** not a literal translation — it provides an "OTT content title localization" context (allows liberal rendering, keeps the character count roughly similar, etc.).

### `dims.py`

PIL-based exact pixel snapping + chroma keying.

```python
def snap(image_bytes: bytes, target: tuple[int, int],
         mode: Literal["crop", "resize"] = "crop") -> bytes:
    """
    - mode='crop': center-crop to the target ratio → Lanczos resize
    - mode='resize': resize directly, ignoring ratio (for alpha PNGs like logos)
    """

def chroma_key_magenta(png_bytes, key_color=(255, 0, 255), tolerance=30,
                       fallback_auto=False) -> bytes:
    """
    Convert a magenta background to transparency (post-processing for title_extract).
    - within tolerance (Chebyshev) → alpha 0, soft ramp out to 2×tolerance
      (reduces anti-aliased edge fringing)
    - fallback_auto=True: if #FF00FF keying covers less than 30%, detect the median
      border color and re-key (handles cases where the model drifts the background to pink)
    """

def to_white(rgba_png_bytes: bytes) -> bytes:
    """Replace RGB with white while preserving alpha. Unused in v1 outputs (the white-logo
    variant is excluded from the spec) — kept for a future variant."""

# Spec constants
SPECS = {
    "portrait":  (900, 1600),
    "landscape": (1600, 900),
    "title":     (580, 200),
    "banner":    (1520, 536),
}
```

**snap() algorithm:**
1. Compute the target ratio against the input dimensions.
2. Center-crop to the target ratio (anchored on one edge).
3. Lanczos resize to exact pixels.
4. Preserve transparent PNGs (keep RGBA).

### `pipeline.py`

Orchestration. Runs STEP B–J (STEP A translation is pre-handled by the UI).

```python
def process(input_path: Path | bytes | str,
            titles: dict[str, str],          # {"kr","en","zh"} — all required
            model_id: str | None = None,     # sidebar dropdown override
            progress_cb: Callable[[int, int, str], None] | None = None,
            slug: str | None = None,
            prompt_overrides: dict[str, str] | None = None,  # UI prompt editor
            precleaned: bytes | None = None, # approved clean base → skip STEP B
            style_notes: str | None = None,  # designer instructions → added to swap/extract
           ) -> dict:
    """Returns {"slug": ..., "outputs": {1: Path, ...}, "meta": {...}}"""

def stage_clean(input_path, model_id=None, prompt_override=None) -> bytes:
    """Run STEP B only — for the staged-review preview. 1 call."""

def regenerate(out_dir, seq, model_id=None, prompt_overrides=None,
               style_notes=None) -> Path:
    """Re-run a single output. titles/model/style_notes are restored from _meta.json;
    sources are the _work/ intermediates plus the upstream output files on disk."""
```

**Note:** language keys are `kr/en/zh` internally (compatible with translate.py), while filename labels are `kr/en/cn` (user spec). The `_OUTPUT_SPECS` table handles the mapping.

**Parallelism:** Phase C (portrait swap ×3) and Phase D (landscape outpaint) are independent → run in parallel via `ThreadPoolExecutor`. Phases E/F/G are parallelized where possible.

**Retry policy:**
- ×3 per Gemini call.
- On whole-step failure, partial results are kept and the failed step is recorded in `_meta.json`.
- Failed outputs can be re-run individually from the UI.

### `app.py`

Streamlit UI. Main components (top to bottom):

1. **Password gate** — active only when `APP_PASSWORD` env/secret is set. Per-session authentication.
2. **Secrets bridge** — copies the cloud's `st.secrets` into `os.environ` (modules read env vars only).
3. **Sidebar** — model dropdown + per-image / per-title cost display.
4. **Input** — source upload, three title fields, "✨ Fill the rest with AI" (translate), style notes, prompt editor (expander).
5. **Staged review** — `1️⃣ Preview text removal` → `stage_clean()` → show result + retry/approve buttons. An expander lets you upload your own clean base to skip STEP B. Uploading a new source automatically invalidates the preview.
6. **Run** — `Generate all` or `Approve and generate the rest` → `pipeline.process(precleaned=...)`, with the 11-step progress shown via progress_cb.
7. **Result grid** — per output 🔍 (preview dialog: original/x2/x4 download) + 🔄 (`pipeline.regenerate()`). ZIP download, failed-step expander, cost summary.

Streamlit widget-constraint pattern: once a widget claims a session_state key you can no longer write to it → "Fill the rest with AI" and prompt reset use a two-phase approach (set a flag, `st.rerun()`, then handle it before the widget is created).

---

## 4. Gemini prompt strategy

**The source of truth is the `prompts/*.txt` files.** Drafts are not pasted into this document (they are tuned continuously in production and would quickly go stale). Only the current key strategies are summarized:

| Prompt | Core strategy |
|---|---|
| `clean.txt` | Remove all text (including the title), preserve the artwork |
| `outpaint_landscape.txt` | Natural left/right extension + **a composition that leaves the lower third clear for the title** (subject slightly above center) |
| `outpaint_banner.txt` | Ultra-wide extension, zero text + **subject on the right, left 40% kept clear for text** |
| `title_swap.txt` | Existing-title replacement pattern. Uses the second image (original) as a typography reference — prevents color/material drift across languages |
| `title_extract.txt` | Render only the title on a **#FF00FF magenta background** → dims.py chroma key. Enforces exact #FF00FF (includes anti-pink-drift instructions) |

Common mechanisms:
- `{title}` and `{language}` placeholders are substituted by gemini.py via `.format()`.
- The content of the UI's "Edit prompts (advanced)" is passed as `prompt_overrides` for per-run override.
- `config.yaml::prompts_override` can swap the prompt file per model.
- Style notes (`style_notes`) are appended as a "Designer notes" section at the end of the swap/extract prompts.

---

## 5. `_meta.json` schema

```json
{
  "slug": "lets_play_soccer_3",
  "source": "local",
  "input": {
    "file": "inputs/upload.jpg",
    "titles": {
      "kr": "뭉쳐야찬다3",
      "en": "Let's Play Soccer 3",
      "zh": "一起踢足球3"
    },
    "style_notes": "keep the gold-foil texture"
  },
  "model": {
    "image_model": "google/gemini-3.1-flash-image-preview",
    "cost_per_image_usd": 0.05
  },
  "outputs": {
    "1": {"path": "ready/lets_play_soccer_3/kr-portrait-title.png",
          "dim": [900, 1600], "lang": "kr", "type": "portrait",
          "variant": "", "status": "ok"},
    "...": "...  (1–11; on failure path=null + status='failed')"
  },
  "stats": {
    "started_at": "2026-06-11T14:30:00Z",
    "finished_at": "2026-06-11T14:34:22Z",
    "duration_sec": 262,
    "api_calls": {"gemini_image": 12},
    "estimated_cost_usd": 0.6,
    "outputs_produced": 11,
    "outputs_expected": 11,
    "regenerations": 2
  },
  "failures": []
}
```

- `regenerations` is incremented by 1 on each `regenerate()` call (tracks per-output regenerations).
- `regenerate()` restores titles/model/style_notes from this file, so per-output regeneration is impossible without `_meta.json`.

---

## 6. Error-handling strategy

| Situation | Handling |
|---|---|
| Transient OpenRouter error (5xx) | exponential-backoff retry ×3 |
| Rate limit (429) | honor `Retry-After` header, then retry |
| Gemini returns no image (text only) | strengthen the prompt and retry ×2 |
| Incomplete body-text removal | retry in the staged-review UI, or upload your own clean base |
| Subject distortion in outpainting | regenerate just that output with the 🔄 button in the result grid |
| Title logo background drifts from #FF00FF | `chroma_key_magenta(fallback_auto=True)` auto-detects the border color for keying |
| Pixel-snap failure (insufficient input dimensions) | inform the user with a clear error message |
| `OPENROUTER_API_KEY` not set | immediate error at app start, with a guide |

---

## 7. `config.yaml` schema

**The current values in [config.yaml](./config.yaml) are the source of truth.** Schema summary:

```yaml
# Model catalog (sidebar dropdown). Models over $0.10/image are disabled by default
# for cost control (the list is kept in the config comments).
models:
  default: "google/gemini-3.1-flash-image-preview"
  available:
    - id: "<openrouter-model-id>"
      label: "<dropdown display name>"
      cost_per_image: 0.05        # estimated unit cost — used in the UI cost display and meta

# Text LLM (translation / language detection). Must be a model your account can access (watch for 403).
text_llm: "google/gemini-2.5-flash"

# Output filename template (vars: {slug} {seq} {lang} {type} {variant} {variant_suffix} {date})
# lang ∈ kr|en|cn|clean, type ∈ portrait|landscape|logo|main-banner
filename_template: "{lang}-{type}-title.png"
# e.g. "kr-portrait-title.png", "clean-landscape-title.png"

# Output folder pattern
output_folder: "ready/{slug}/"

# Pixel specs (overridable — changing them makes the whole pipeline output at that size)
specs:
  portrait:  [900, 1600]
  landscape: [1600, 900]
  title:     [580, 200]
  banner:    [1520, 536]

# Per-model prompt overrides (optional)
prompts_override: {}
```

---

## 8. v2 extension hooks

Interfaces prepared ahead of time in the v1 code:

- `pipeline.process()` can accept `bytes` instead of `input_path` → process bytes downloaded from Drive directly.
- `save_outputs()` is factored out → replaceable with a Drive uploader in v2.
- The `_meta.json` schema reserves a `source: "local"|"sheet"` field → track sheet row id in v2.
- Model-call functions are parameterized by `model_id` → pass the sidebar selection straight through in v2.
- ~~Secrets abstracted via `os.getenv()`~~ → ✅ applied: app.py bridges `st.secrets` → `os.environ` (in use for the Streamlit Cloud deployment).

---

## 9. Validation plan (after coding starts)

1. **Unit behavior checks** (during development)
   - Does `gemini.clean()` return a body-text-removed result?
   - Does `dims.snap()` hit exact pixel dimensions?
2. **End-to-end first title** (when development is complete)
   - Input one real CP poster → verify 11 outputs
   - Designer review → record pass rate and rework items
3. **5-title pilot** (first week of launch)
   - Measure pass rate, cost, and time → decide whether the KR targets are met
   - Tune prompt and dimension policy

---

## 10. Resolved open questions

| # | Question | Answer | Reflected in |
|---|---|---|---|
| Q1 | If the input is an EN/CN portrait, still generate a fresh KR portrait? | **YES** — always clean → swap × 3 regardless of input language | STEP B+C, Appendix A |
| Q2 | Where does the CN banner title go? | **N/A** — the banner is a clean, text-free version | STEP F (G step removed), prompts/outpaint_banner.txt |
| Q3 | Is clean landscape completely text-free? | **YES** | prompts/clean.txt (kept) |
| Q4 | Title logo colors? | ~~original + white~~ → **original only** (white variant excluded once spec was finalized) | outputs #8–10, `dims.to_white()` kept but unused |
| Q5 | Where are designer review results recorded? | **TBD** | decide during v1.5 design |
```