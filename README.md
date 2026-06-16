# Poster Automation System v1

## Overview

A Streamlit tool for the ODK Media PMO team. Feed it one portrait poster received from a CP, and it automatically produces **11 variants** spanning languages (KR/EN/CN) × orientations (portrait/landscape) × title logos. It combines an image model via OpenRouter (default: the Gemini Flash Image family) with PIL post-processing to output exact pixel specs.

- Designer usage guide: [WORKFLOW.md](./WORKFLOW.md)
- Technical design: [ARCHITECTURE.md](./ARCHITECTURE.md)
- Tech stack reference: [STACK.md](./STACK.md)
- Requirements: [PRD-poster-automation.md](./PRD-poster-automation.md)

## Quick start (local)

1. Clone the repo (or download the folder).
2. Create and activate a virtualenv, then install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Configure `.env`:
   ```bash
   cp .env.example .env
   # enter OPENROUTER_API_KEY in the .env file
   ```
4. Run:
   ```bash
   streamlit run app.py
   ```
   The browser opens automatically (usually http://localhost:8501).

## Web deployment (Streamlit Community Cloud)

Connect a GitHub repo and deploy from share.streamlit.io. Settings:

- **Main file path**: `app.py` / **Branch**: `main`
- **Secrets** (app Settings → Secrets, TOML format):
  ```toml
  OPENROUTER_API_KEY = "sk-or-v1-..."
  APP_PASSWORD = "team_shared_password"   # optional — if set, requires a password on access
  ```
- `runtime.txt` pins the Python version.
- The cloud disk is ephemeral — `ready/` outputs disappear on app restart, so download them as a ZIP or individually.

## Workflow

Upload poster → enter three language titles (or "Fill the rest with AI") → style notes (optional) → generate → review → download.

Two ways to generate:

- **Staged review (recommended)**: run `1️⃣ Preview text removal` (1 call) to inspect the clean result first, then `✅ Approve and generate the rest` (11 calls). You can also upload your own Photoshop-cleaned base to skip step 1.
- **Generate all (skip review)**: run all 12 calls at once.

In the result grid:
- 🔍 — original-size preview + original/x2/x4 upscale download
- 🔄 — regenerate just that output (1 call; when regenerating #7, regenerating #4·5·6·11 is recommended)

## Outputs

`ready/{slug}/` contains 11 PNGs + `_meta.json` + `_work/` (intermediate artifacts for regeneration).

| # | File | Size |
|---|---|---|
| 1–3 | `kr/en/cn-portrait-title.png` | 900×1600 |
| 4–6 | `kr/en/cn-landscape-title.png` | 1600×900 |
| 7 | `clean-landscape-title.png` | 1600×900 (no text) |
| 8–10 | `kr/en/cn-logo-title.png` | 580×200 (transparent background) |
| 11 | `cn-main-banner-title.png` | 1520×536 (no text) |

`_meta.json` records the input info, output paths and dimensions, API call counts, estimated cost, failed steps, and regeneration count (schema in ARCHITECTURE.md §5).

## Customizing configuration

Change these in `config.yaml`:

- **Default AI model and catalog** (`models.default`, `models.available`) — surfaced in the sidebar dropdown. Models over $0.10/image are disabled by default for cost control.
- **Text LLM** (`text_llm`) — used for language detection and translation (default `google/gemini-2.5-flash`).
- **Filename template** (`filename_template`) — variables: `{slug}`, `{seq}`, `{lang}`, `{type}`, `{variant}`, `{variant_suffix}`, `{date}`.
- **Output folder pattern** (`output_folder`) — default `ready/{slug}/`.
- **Pixel specs** (`specs`) — override portrait/landscape/title/banner dimensions.
- **Per-model prompt overrides** (`prompts_override`) — map a model id to per-step prompt file paths.

The prompts themselves default to `prompts/*.txt`, and can be overridden per run from the UI's "Edit prompts (advanced)".

## Tests

```bash
pip install pytest
python3 -m pytest tests/ -q
```

All API calls are mocked — runs without an API key.

## Cost

- 12 image calls × ~$0.05 + 3 text calls × $0.001 ≈ **~$0.60 per title** (default model)
- Using the staged review catches clean-step failures early, saving re-run cost
- 10 titles/week ≈ ~$6/week
- Meets the PRD KR5 target (under $1 per title)

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Environment variable OPENROUTER_API_KEY is not set` | `.env` missing or key not entered (cloud: secrets unset) | run `cp .env.example .env` and enter the key; on cloud use Settings → Secrets |
| Generate button is disabled | source not uploaded or one of the three titles is blank | check the upload + use "Fill the rest with AI" or enter manually |
| Auto-translation fails (403, etc.) | OpenRouter text LLM not accessible | change `text_llm` in `config.yaml` to a model your account can access |
| 5xx / 429 OpenRouter transient error | server hiccup or rate limit | after the automatic ×3 retry still fails, wait a bit and re-run (partial results are kept) |
| Fewer than 11 outputs | some steps failed | check "failed steps" on the result screen, then 🔄 regenerate those outputs |
| Incomplete body removal / outpaint distortion | model quality issue | retry in step-1 review, or 🔄 regenerate just that output |
| Title logo is opaque / has a purple edge | model drifted the background color | auto-correction included — if it persists, 🔄 regenerate that logo |
| Per-output regeneration fails (`_work/source.png not found`) | output from an older version | run a full generation once more |
| `client error 413: ... 30MB` | exceeds the OpenRouter 30MB input limit | downscale to ~2048px before uploading, or see "High-quality source handling" below |
| `streamlit: command not found` | virtualenv not activated | run `source .venv/bin/activate` and try again |

## High-quality source handling (optional)

To bypass the OpenRouter 30MB limit, enable the direct Google AI Studio backend. Add `GOOGLE_API_KEY` to `.env` (or the cloud Secrets) and it switches automatically; inputs over 18MB are uploaded via the Google Files API before processing.

```
GOOGLE_API_KEY=AIza...   # https://aistudio.google.com/app/apikey
```

Without the key it keeps using OpenRouter (default).

## Roadmap

- **v1**: local Streamlit ✅
- **v1.5**: staged review ✅ · per-output regeneration ✅ · style notes ✅ · web deployment (Streamlit Cloud) + shared password ✅ — remaining: cost/time dashboard, CSV batch mode
- **v2**: Google SSO, Google Sheets input, Google Drive output upload, parallel multi-title processing, AI upscaling (Real-ESRGAN) integration
