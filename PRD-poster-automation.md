# PRD — OTT Poster Automation System

## 1. Overview

The ODK Media PMO team manually produces a full set of poster variants for every new OTT title — across languages (KR/EN/CN), orientations (portrait/landscape), and use cases (clean base, title logos, Chinese banner). At ~10 new titles a week and ~2 hours of designer time each, this is slow, repetitive, and error-prone (a single pixel-spec mistake gets a platform rejection).

This system automates it. A designer uploads one portrait poster from a content provider, enters the title in any one language, and the tool produces **11 delivery-ready variants** at exact pixel specs. It combines an AI image model (Gemini Flash Image via OpenRouter) with local PIL post-processing, wrapped in a Streamlit web app that non-technical staff can self-serve.

**Why now.** The Gemini Flash Image family ("Nano Banana") can remove poster text, outpaint to new aspect ratios, and compose multi-language titles with a single model at ~$0.04–0.05 per image. Unified APIs like OpenRouter make models swappable and billing consolidated. Until recently, cost, quality, or multilingual text rendering each fell short, making this impractical.

Phase 1 (the core 11-output generator) is **complete and in use**. Phase 2 adds automated finishing and delivery: upscale → resize/crop to spec → upload to Google Drive.

---

## 2. Goals & Success Metrics

Shift poster-variant production from manual to automated to (1) cut PMO/design effort and (2) shorten the lead time to launch a title. This removes a bottleneck in ODK's simultaneous global-release strategy.

| KR | Metric | Target | Timeframe |
|---|---|---|---|
| KR1 | Time to produce the full variant set per title | manual ~2 hours → under 10 minutes | within 3 months of v1 launch |
| KR2 | Cumulative titles processed | 40+ | first 4 weeks of operation |
| KR3 | Designer rework rate (share of outputs edited or regenerated) | 30% or less | measured at week 4 |
| KR4 | Output pixel-spec accuracy | 100% (zero pixel error vs. target dimensions) | at v1 launch |
| KR5 | API cost per title | under $1 | ongoing |

---

## 3. Features

All Phase 1 features are **delivered**. Phase 2 features are detailed in §4.

**Generation pipeline**
- Language auto-detection + translation of the title into all three languages (manual override always available).
- Body-text removal to a clean base, orientation outpainting (portrait → landscape), and ultra-wide banner outpainting.
- Per-language title composition (portrait + landscape) and transparent title-logo extraction.
- Exact pixel snapping (center-crop + Lanczos) so every output matches its delivery spec.

**Workflow & control**
- Staged review: preview the text-removal step, approve (or retry / upload your own clean base) before committing the rest.
- Per-output regeneration (🔄) using persisted intermediates — no full re-run needed.
- Style notes injected into the compose/extract prompts.
- Sidebar model selection (swap image models via OpenRouter); per-run prompt overrides.

**Output & access**
- 11 outputs + `_meta.json` per title; ZIP and per-file download.
- x2/x4 Lanczos upscale-on-download from the preview popup.
- Optional shared-password gate on the deployed app.

---

## 4. Phases / Roadmap

### Phase 1 — Core automation ✅ (complete, unchanged)

The full 11-output generator described in §3, running as a Streamlit app (local + hosted on Streamlit Community Cloud), with staged review, per-output regeneration, retries, and metadata. **No changes planned.**

### Phase 2 — Automated finishing & Drive delivery (next)

Automate the manual "finish and file" steps after generation: take each approved output, sharpen it, conform it to spec, and deliver it to Google Drive in the right place — with no manual file handling.

**Flow:**
1. Designer generates and reviews the 11 outputs (Phase 1), marking outputs as approved.
2. Designer selects the destination Google Drive folder for the run.
3. For each **approved** output: **upscale (Lanczos x2/x4, already built) → resize/crop back to the same 11 delivery specs** — same dimensions, sharper result.
4. Once outputs are approved **and** a destination is selected, upload runs **automatically** (no extra click).
5. Files are organized **per title, subfoldered by type**: a title folder containing `portrait/`, `landscape/`, `logo/`, `banner/` subfolders.

**Decisions captured:**

| Aspect | Decision |
|---|---|
| Upscaling method | Lanczos x2/x4 (reuse the existing upscale; no new AI dependency) |
| Resize/crop target | The same 11 specs, sharper — not new sizes |
| Drive folder layout | Per-title folder, subfoldered by asset type |
| Destination | A Drive folder picked per run in the UI |
| Upload trigger | Automatic, gated on output approval + a selected destination |
| Authentication | User Google sign-in (OAuth), as the designer's `@odkmedia.net` account |

**Also in Phase 2 scope:** API cost guards (per-user/day limits), and Google Drive output upload solving the ephemeral-disk problem of the hosted app.

### Phase 3 — Expansion (longer term)

- Google Sheets input (Drive URL + title columns) and sheet-triggered batch runs.
- Parallel multi-title processing and background jobs.
- Additional languages (Vietnamese, Thai, …) and automatic model routing on quality misses.
- Per-platform spec presets (Netflix / TVING / iQIYI, …).

---

## 5. Assumptions (to validate)

| ID | Assumption | Validation |
|---|---|---|
| A1 | The image model produces KR/EN/CN typography that meets OTT distribution standards | designer review of the first 5 titles |
| A2 | Portrait → landscape outpainting passes on the first try 70%+ of the time | measure pass rate over the first 10 titles |
| A3 | CP source resolution is large enough (at least 1080×1920) | sample past assets |
| A4 | The 1520×536 banner ratio works across major Chinese platforms (iQIYI, Tencent, …) | confirm with ops team |
| A5 | Lanczos upscale-then-resize is sufficient sharpening (no AI super-resolution needed) | designer review on Phase 2 pilot |
| A6 | Designers' `@odkmedia.net` Google accounts have write access to the target Drive folders | confirm with IT/ops |

---

## Appendix A. Output spec matrix (11 outputs)

| # | Output | Dimension | Background | Notes |
|---|---|---|---|---|
| 1 | KR portrait | 900×1600 | original base | clean base + KR title (always regenerated, regardless of input language) |
| 2 | EN portrait | 900×1600 | original base | clean base + EN title |
| 3 | CN portrait | 900×1600 | original base | clean base + CN title |
| 4 | KR landscape | 1600×900 | outpainted | clean landscape + KR title |
| 5 | EN landscape | 1600×900 | outpainted | clean landscape + EN title |
| 6 | CN landscape | 1600×900 | outpainted | clean landscape + CN title |
| 7 | Clean landscape | 1600×900 | outpainted | **no text** (thumbnails / web headers) |
| 8 | KR title logo | 580×200 | transparent | alpha PNG, original color |
| 9 | EN title logo | 580×200 | transparent | alpha PNG, original color |
| 10 | CN title logo | 580×200 | transparent | alpha PNG, original color |
| 11 | CN main banner | 1520×536 | outpainted | **no text**, Chinese-platform hero slot |

Filenames follow `{lang}-{type}-title.png`, e.g. `kr-portrait-title.png`, `clean-landscape-title.png`, `cn-main-banner-title.png`. (The white title-logo variant from earlier drafts was dropped — color logos only; 11 outputs, not 14.)

## Appendix B. Cost estimate

- Image calls (default model): 12 per title — clean portrait ×1, portrait title swap ×3, landscape outpaint ×1, landscape title swap ×3, banner outpaint ×1, title extract ×3.
- Image call cost: 12 × ~$0.05 = **~$0.60**; text calls (detect + translate): ~$0.003.
- Chroma keying, pixel snapping, and Phase 2 upscale/resize run locally in PIL — **no API cost**.
- **~$0.60 per title**, meets the under-$1 target. ~10 titles/week ≈ **~$6/week** (~$24/month).
- Staged review can drop one image call (skip clean) when a pre-approved base is reused.

## Appendix C. Generation flow & data lineage

Each output is produced by an AI image-edit call. What matters for quality is **which image each call edits** (the "primary" input) and whether the **original source** is involved — directly, as a derived base, or as a styling reference.

```
                         ┌─────────────────────────────┐
   original source ──────┤ edited DIRECTLY              │
        │                │  • clean base               │
        │                │  • portraits #1/2/3         │
        │                └─────────────────────────────┘
        │
        ├──► clean base ──► clean landscape #7 ──► landscape titles #4/5/6
        │                                      └─► banner #11
        │
        └──► portrait swaps #1/2/3 ──► logos #8/9/10

   (original is also passed as a STYLING REFERENCE to the language-sensitive
    derived steps: landscape titles #4/5/6 and logos #8/9/10)
```

| Output | Primary input (edited) | Original source used? |
|---|---|---|
| Clean base | original source | ✅ directly |
| Portraits #1/2/3 | original source (title-swap replaces the existing title, so the model must see it) | ✅ directly |
| Clean landscape #7 | clean base (derived) | ❌ derived only |
| Landscapes #4/5/6 | clean landscape #7 (derived) | ⚠️ passed as styling reference |
| Banner #11 | clean landscape #7 (derived) | ❌ derived only |
| Logos #8/9/10 | portrait swap #1/2/3 (derived) | ⚠️ passed as styling reference |

**Why the chains are necessary.**
- Landscapes and the banner must be **text-free first**, so they are outpainted from the clean base, not the original.
- A logo for a given language can only be extracted from an image that **already carries that language's title** — i.e. the matching portrait swap. The original carries only one language, so it cannot be used directly for all three logos.

**Drift control.** For the two language-sensitive derived steps (landscape titles, logos), the **original is passed as a second reference image** so typography, color, and material do not drift across generations. The text-free outputs (#7 clean landscape, #11 banner) are pure derivations with no reference, since styling drift matters least there.

**Residual risk.** Landscapes are ~3 generations removed from the original (source → clean → landscape → title), so any cumulative model drift is most likely to appear there. This is a quality trade-off inherent to the chain, not a defect; per-output regeneration (🔄) is the mitigation.

**Source language is arbitrary (KR, EN, or CN).** The pipeline never branches on the source language: the clean step removes all text regardless of script, and `title_swap` is explicitly instructed to preserve color/material even across scripts (Latin → CJK, CJK → Latin). So a Chinese source poster produces the full KR/EN/CN set just as a Korean one would. One fidelity nuance to expect:

- The output **matching the source language** (e.g. the CN outputs for a Chinese source) is the "native" rendering — closest to the original, highest fidelity.
- The **other two languages** are cross-script re-renders: the model adapts the original title's styling onto different glyphs. Quality is usually good but is exactly what Assumption **A1** asks to validate on the first few titles.
