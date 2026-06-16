# Poster Automation — Designer Usage Guide

One OTT portrait poster → **11 multi-language (KR/EN/CN) posters, banners, and title logos**, generated automatically.

> For the developer-facing technical doc see [ARCHITECTURE.md](./ARCHITECTURE.md); for installation see [README.md](./README.md).

## 1. Access

- **Web**: open the deployed Streamlit app URL (if a password is set, enter the team shared password)
- **Local**: `streamlit run app.py` (see README for setup)

## 2. Basic flow

```
Upload source → enter 3 titles → (style notes) → generate → review → download
```

### ① Upload the source poster
- One portrait poster. JPG/PNG.
- At least 1080×1920 recommended (smaller inputs lose quality during upscaling).

### ② Enter titles
- All three fields — Korean, English, Chinese — are required.
- Enter just one and press **✨ Fill the rest with AI** to auto-translate the others.
- Auto-translation may differ from the officially licensed title, so always check and edit.

### ③ Style notes (optional)
- Freely enter instructions about the title design. They are passed straight into the AI prompt.
- e.g. `keep gold-foil texture` / `make English title slightly smaller` / `keep brush-stroke feel`

### ④ Generate — two ways

**Method A — Staged review (recommended)**
1. Click **1️⃣ Preview text removal** → runs only step 1 (text removal) (~$0.05)
2. Check the result: is the body text cleanly removed? is the artwork undamaged?
   - If not, click **🔄 Remove again**
   - If you have a Photoshop-cleaned base, you can substitute it via **Upload your own clean base**
3. Click **✅ Approve and generate the rest** → runs the remaining 11 calls

> The clean result is the basis for all landscape and banner outputs, so catching issues here greatly reduces cost and rework.

**Method B — All at once**
- Click **Generate all (skip review)** → runs all 12 calls at once (~$0.60)

### ⑤ Review results
- The 11 outputs are shown in a grid.
- **🔍 filename button** → original-size preview popup (ESC to close)
  - Inside the popup you can download **original / x2 / x4 upscale** (Lanczos method)
- **🔄 button** → regenerate just that output (1 call, ~$0.05)
  - ⚠️ **If you regenerate #7 (clean landscape)**, the basis for #4·5·6 (landscape) and #11 (banner) changes, so regenerating those four is recommended

### ⑥ Download
- **Download ZIP** — all 11 PNGs + `_meta.json`
- Or download individually from the preview popup
- When running locally, you can also access the `ready/<title>/` folder directly

## 3. Output list

| # | Filename | Size | Description |
|---|---|---|---|
| 1 | `kr-portrait-title.png` | 900×1600 | Korean portrait |
| 2 | `en-portrait-title.png` | 900×1600 | English portrait |
| 3 | `cn-portrait-title.png` | 900×1600 | Chinese portrait |
| 4 | `kr-landscape-title.png` | 1600×900 | Korean landscape |
| 5 | `en-landscape-title.png` | 1600×900 | English landscape |
| 6 | `cn-landscape-title.png` | 1600×900 | Chinese landscape |
| 7 | `clean-landscape-title.png` | 1600×900 | Text-free landscape |
| 8 | `kr-logo-title.png` | 580×200 | Korean title logo (transparent background) |
| 9 | `en-logo-title.png` | 580×200 | English title logo (transparent background) |
| 10 | `cn-logo-title.png` | 580×200 | Chinese title logo (transparent background) |
| 11 | `cn-main-banner-title.png` | 1520×536 | Chinese main banner (no text, left margin) |

- The prompts are written so landscape leaves the lower third, and the banner leaves the left 40%, clear for text/UI.
- Title logos are transparent PNGs. If you see purple residue at the edges, 🔄 regenerate just that logo.

## 4. Cost reference (default model, ~$0.05 per image)

| Action | Calls | Cost |
|---|---|---|
| Preview text removal | 1 | ~$0.05 |
| Approve and generate the rest | 11 | ~$0.55 |
| Generate all (skip review) | 12 | ~$0.60 |
| Per-output regeneration | 1 | ~$0.05 |
| Auto-translation | 3 text | ~$0.003 |

The model can be changed in the sidebar (its price is shown too).

## 5. Common issues

| Symptom | Fix |
|---|---|
| Body text not fully removed | **🔄 Remove again** in step-1 review; if it keeps failing, upload your own Photoshop clean base |
| One output looks off (subject distortion, weird typography) | regenerate it with its **🔄** button |
| Title logo isn't transparent / has a purple edge | 🔄 regenerate that logo (auto-correction included) |
| Title color/texture differs across languages | specify it in style notes (e.g. "keep all languages gold") and regenerate |
| Auto-translation differs from the official title | edit the field directly (auto-translation is for reference) |
| Generate button disabled | requires source upload + all 3 titles filled in |
| Landscape composition isn't to your liking | regenerate #7 → then regenerate 4·5·6·11 |
| Need higher resolution | download x2/x4 from the preview popup; for higher quality use a dedicated tool like Gigapixel |
