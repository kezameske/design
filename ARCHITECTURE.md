# Architecture — OTT 포스터 자동화 시스템 v1

PRD: [PRD-poster-automation.md](./PRD-poster-automation.md)

이 문서는 개발자용 기술 설계서다. 파일 구조·데이터 흐름·모듈 책임·Gemini 프롬프트 초안·에러 처리 전략을 다룬다.

---

## 1. 파일 구조

```
design/
├── PRD-poster-automation.md     # 이 PRD
├── ARCHITECTURE.md              # 이 문서
├── WORKFLOW.md                  # 기존 v0 가이드 (참조용, v1에서 deprecated)
├── requirements.txt             # 의존성
├── .env.example                 # OPENROUTER_API_KEY 예시
├── config.yaml                  # 모델 목록, 파일명 템플릿, 프롬프트 override
├── README.md                    # 설치·실행 가이드
│
├── app.py                       # Streamlit UI 엔트리포인트
├── pipeline.py                  # 11-output 오케스트레이션
├── gemini.py                    # Gemini 이미지 API 래퍼
├── translate.py                 # 언어 감지 + 번역
├── dims.py                      # PIL 픽셀 스냅 유틸
│
├── prompts/                     # Gemini 프롬프트 템플릿
│   ├── clean.txt
│   ├── outpaint_landscape.txt
│   ├── outpaint_banner.txt
│   ├── title_swap.txt
│   └── title_extract.txt
│
├── inputs/                      # 사용자 업로드 임시 저장
└── ready/
    └── <slug>/                  # 타이틀당 출력 폴더 (14개)
        ├── 01_kr_portrait.png        # 900x1600
        ├── 02_en_portrait.png
        ├── 03_cn_portrait.png
        ├── 04_kr_landscape.png       # 1600x900
        ├── 05_en_landscape.png
        ├── 06_cn_landscape.png
        ├── 07_clean_landscape.png    # 텍스트 없음
        ├── 08_kr_title_color.png     # 580x200 transparent (원색)
        ├── 09_kr_title_white.png     # 580x200 transparent (흰색)
        ├── 10_en_title_color.png
        ├── 11_en_title_white.png
        ├── 12_cn_title_color.png
        ├── 13_cn_title_white.png
        ├── 14_cn_banner_clean.png    # 1520x536, 텍스트 없음
        └── _meta.json
```

---

## 2. 데이터 흐름 (타이틀 1개당)

```
[입력]
  portrait.jpg + 타이틀 문자열 (KR/EN/CN 중 1개)
        │
        ▼
┌─────────────────────────────────────────────────┐
│ STEP A. 언어 감지 + 번역                          │
│   translate.detect(title) → src_lang             │
│   translate.translate(title, src_lang, [others]) │
│     → {kr: "...", en: "...", zh: "..."}          │
└─────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────┐
│ STEP B. clean_portrait 생성 (1회 호출)            │
│   gemini.clean(portrait) → clean_portrait        │
│   ※ 본문·타이틀 모두 제거된 베이스                │
└─────────────────────────────────────────────────┘
        │
        ├─────────────────────────────┐
        ▼                             ▼
┌──────────────────────┐   ┌──────────────────────┐
│ STEP C. 포트레이트     │   │ STEP D. clean_       │
│ 타이틀 합성 × 3        │   │ landscape 생성        │
│  for lang in {kr,en,  │   │  gemini.outpaint(    │
│    zh}:               │   │    clean_portrait,   │
│    gemini.title_swap( │   │    "landscape")      │
│      clean_portrait,  │   │  → 출력 #7 (clean)   │
│      titles[lang])    │   │                      │
│  → 출력 #1, #2, #3    │   │                      │
└──────────────────────┘   └──────────────────────┘
        │                              │
        │                   ┌──────────┴──────────┐
        │                   ▼                     ▼
        │         ┌──────────────────┐  ┌──────────────────┐
        │         │ STEP E. 랜드스케  │  │ STEP F. CN 배너   │
        │         │ 이프 타이틀 합성   │  │ outpaint          │
        │         │  × 3              │  │  gemini.outpaint( │
        │         │  → 출력 #4, #5, #6│  │    clean_         │
        │         └──────────────────┘  │    landscape,     │
        │                               │    "banner")      │
        │                               │  → 출력 #14       │
        │                               │  (텍스트 합성 없음)│
        │                               └──────────────────┘
        ▼
┌─────────────────────────────────────────────────┐
│ STEP G. 타이틀 로고 추출 × 3 (원색)               │
│   for lang in {kr, en, zh}:                      │
│     color_logo = gemini.title_extract(           │
│                    portraits[lang])              │
│   → 출력 #8, #10, #12                            │
└─────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────┐
│ STEP H. 흰색 로고 변환 × 3 (PIL, API 호출 없음)   │
│   for color_logo in [#8, #10, #12]:              │
│     white_logo = dims.to_white(color_logo)       │
│       ※ 알파 채널 유지, RGB → (255,255,255)      │
│   → 출력 #9, #11, #13                            │
└─────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────┐
│ STEP I. 픽셀 스냅 (모든 출력 14개)                │
│   for each output:                               │
│     dims.snap(image, target_dim)                 │
│   → 정확한 픽셀 dimension 보장                    │
└─────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────┐
│ STEP J. 저장 + _meta.json 기록                   │
└─────────────────────────────────────────────────┘
```

**총 Gemini 호출:** 1(clean) + 3(portrait swap) + 1(landscape outpaint) + 3(landscape swap) + 1(banner outpaint) + 3(title extract) = **12회**

**총 LLM 호출:** 1(감지) + 2(번역) = **3회**

**총 PIL 처리:** 3(흰색 변환) + 14(픽셀 스냅) = 17회 (모두 로컬, 무료)

---

## 3. 모듈 책임

### `gemini.py`

OpenRouter 경유 `google/gemini-2.5-flash-image` 단일 모델 호출 래퍼.

```python
def clean(image_bytes: bytes) -> bytes:
    """포스터에서 본문·타이틀 모두 제거. clean base 반환."""

def outpaint(image_bytes: bytes, target: Literal["landscape", "banner"]) -> bytes:
    """비율 변환. landscape=16:9, banner=1520:536 비율 outpainting."""

def title_swap(clean_base: bytes, title: str, lang: str) -> bytes:
    """clean base 위에 지정 언어의 타이틀 텍스트 합성."""

def title_extract(poster: bytes, lang: str) -> bytes:
    """포스터에서 타이틀만 분리한 transparent PNG."""

def _call_openrouter(prompt: str, image: bytes, **kwargs) -> bytes:
    """공통: multipart 요청 → base64 응답 → bytes."""
```

**중요 구현 디테일:**
- 모든 함수가 **타겟보다 1.2× 큰 사이즈로 생성 요청** → dims.py가 정확 픽셀로 스냅
- 호출 실패 시 자동 재시도 ×3 (exponential backoff)
- 응답 파싱 실패(이미지 미반환) 시도 retry 대상

### `translate.py`

OpenRouter 경유 텍스트 LLM (`openai/gpt-4o-mini`).

```python
def detect(title: str) -> Literal["kr", "en", "zh"]:
    """타이틀 문자열에서 언어 추정."""

def translate(title: str, src: str, targets: list[str]) -> dict[str, str]:
    """src 언어 → targets 언어들로 번역. {lang: translated} 반환."""
```

**프롬프트 전략:** 단순 번역이 아닌 "OTT 콘텐츠 타이틀 현지화" 컨텍스트 제공 (의역 허용, 글자 수 비슷하게 유지 등).

### `dims.py`

PIL 기반 정확한 픽셀 스냅 + 알파 색상 변환.

```python
def snap(image_bytes: bytes, target: tuple[int, int],
         mode: Literal["crop", "resize"] = "crop") -> bytes:
    """
    - mode='crop': 타겟 비율로 center-crop → Lanczos resize
    - mode='resize': 비율 무시하고 직접 resize (로고 등 알파 PNG용)
    """

def to_white(rgba_png_bytes: bytes) -> bytes:
    """
    알파 채널 유지하며 모든 non-transparent 픽셀의 RGB를 (255,255,255)로 치환.
    타이틀 로고 색상 변환용. 알파 그래디언트(부드러운 가장자리)는 보존.
    """

# 스펙 상수
SPECS = {
    "portrait":  (900, 1600),
    "landscape": (1600, 900),
    "title":     (580, 200),
    "banner":    (1520, 536),
}
```

**snap() 알고리즘:**
1. 입력 dimension과 target 비율 계산
2. target 비율로 center-crop (한쪽 변 기준)
3. Lanczos로 정확한 픽셀로 resize
4. transparent PNG 보존 (RGBA 유지)

**to_white() 알고리즘:**
1. RGBA 모드로 로드
2. 알파 채널 분리·보존
3. RGB 채널 전체를 (255,255,255)로 치환
4. 알파 채널 재결합 → 흰색 단색 + 원본 형태 유지

### `pipeline.py`

오케스트레이션. STEP A~J 순차 실행.

```python
def process(input_path: Path | bytes | str,
            titles: dict[str, str],          # {"kr": "...", "en": "...", "zh": "..."} — 모두 필수
            model_id: str | None = None,     # 사이드바 드롭다운 override (else config default)
            progress_cb: Callable[[int, int, str], None] | None = None,
            slug: str | None = None,         # 출력 폴더 슬러그 override
           ) -> dict:
    """
    Args:
        input_path: 파일 경로 또는 raw bytes (v2 Drive bytes hook 호환)
        titles: 3개 언어 타이틀 dict — UI가 입력 수집을 보장
        model_id: 매 호출 모델 선택 (사이드바 드롭다운)
        progress_cb: (current_step, 14, message) 호출
        slug: 미지정 시 EN 타이틀에서 자동 생성
    Returns:
        {"slug": "...", "outputs": {1: Path, 2: Path, ...}, "meta": {...}}
    """
```

**참고:** 언어 키는 내부적으로 `kr/en/zh` (translate.py 호환)이고, 파일명 라벨은 `kr/en/cn` (사용자 spec). 매핑은 `_OUTPUT_SPECS` 테이블이 담당.

**병렬화:** Phase C(포트레이트 합성 ×3)와 Phase D(landscape outpaint)는 독립 → `ThreadPoolExecutor`로 병렬. Phase E/F/G/H 도 가능한 곳 병렬화.

**Retry 정책:**
- 각 Gemini 호출당 ×3
- 전체 step 실패 시 부분 결과 보관 + `_meta.json`에 실패 step 기록
- UI에서 "실패한 출력만 재실행" 가능

### `app.py`

Streamlit UI.

```python
# pseudocode
st.file_uploader("portrait 포스터 업로드")
title = st.text_input("타이틀 (KR/EN/CN 중 1개)")
with st.expander("수동 타이틀 (선택)"):
    en_override = st.text_input("English")
    zh_override = st.text_input("中文")

if st.button("생성"):
    progress = st.progress(0)
    status = st.empty()

    def cb(cur, total, msg):
        progress.progress(cur / total)
        status.write(f"{cur}/{total} — {msg}")

    result = pipeline.process(uploaded_path, title,
                              overrides=..., progress_cb=cb)

    cols = st.columns(4)
    for i, output_path in result["outputs"].items():
        with cols[(i-1) % 4]:
            st.image(output_path, caption=f"#{i}")

    st.download_button("ZIP 다운로드", make_zip(result))
```

---

## 4. Gemini 프롬프트 초안

### `prompts/clean.txt`

```
Remove ALL text from this poster image — including titles, subtitles,
body copy, taglines, logos, ratings, and credits. Preserve the original
artwork: characters, background, composition, color grading, lighting.
The result should look like the same poster with all text invisibly
erased, leaving a clean, text-free image suitable for adding new text
later. Maintain the original aspect ratio and resolution.
```

### `prompts/outpaint_landscape.txt`

```
Convert this portrait poster (900x1600 aspect) into a landscape poster
(1600x900 aspect) by extending the scene horizontally. Preserve the main
characters and central subject. Extend the background naturally on both
sides — maintain the same art style, color palette, lighting, and mood.
The result must be a coherent landscape composition, not a stretched
portrait. Generate at approximately 1920x1080 resolution.
```

### `prompts/outpaint_banner.txt`

```
Convert this landscape image into an ultra-wide CLEAN banner (1520x536
aspect, roughly 2.83:1) with NO TEXT WHATSOEVER. Extend the scene
horizontally further while preserving the main subject in the central
area. Background should extend naturally on both sides. The result must
contain zero text, zero typography, zero logos — just the pure artwork
background suitable for adding text later in design tools. Generate at
approximately 1824x643 resolution.
```

### `prompts/title_swap.txt`

```
This is a clean poster base with no text. Add the title "{title}" in
{language} as the main title of the poster. Place it in a visually
prominent location appropriate for an OTT (streaming service) poster —
typically lower-center or lower-third. Use professional typography
appropriate for the genre and tone of the artwork. The title should be
highly legible and visually integrated with the artwork. Do not add any
other text (no subtitles, taglines, ratings, or credits).

Language guidance:
- kr: Korean typography — clean modern sans-serif or stylized serif
- en: English typography — bold sans-serif preferred for OTT
- zh: Simplified Chinese (简体中文) — clean modern typography
```

### `prompts/title_extract.txt`

```
Extract ONLY the main title text "{title}" from this poster as a
transparent PNG with alpha channel. The output should contain just the
title characters in their original styling (font, color, effects),
isolated from the background. The background must be fully transparent.
Output dimensions approximately 696x240 (will be resized to 580x200).
```

**주의:** 모든 프롬프트는 첫 운영 5타이틀에서 튜닝 예상. 디자이너 검수 결과를 프롬프트에 반영.

---

## 5. `_meta.json` 스키마

```json
{
  "slug": "munchyochanda3",
  "input": {
    "file": "inputs/upload_20260603_1430.jpg",
    "detected_lang": "kr",
    "titles": {
      "kr": "뭉쳐야찬다3",
      "en": "Let's Play Soccer 3",
      "zh": "一起踢足球3"
    }
  },
  "outputs": {
    "1": {"path": "ready/munchyochanda3/01_kr_portrait.png", "dim": [900, 1600], "status": "ok"},
    "2": {"path": "...", "dim": [900, 1600], "status": "ok"},
    "...": "..."
  },
  "stats": {
    "started_at": "2026-06-03T14:30:00Z",
    "finished_at": "2026-06-03T14:34:22Z",
    "duration_sec": 262,
    "api_calls": {"gemini_image": 13, "openrouter_text": 3},
    "retries": {"step_D_outpaint_landscape": 1},
    "estimated_cost_usd": 0.52
  },
  "failures": []
}
```

---

## 6. 에러 처리 전략

| 상황 | 처리 |
|---|---|
| OpenRouter 일시 오류 (5xx) | exponential backoff 재시도 ×3 |
| Rate limit (429) | `Retry-After` 헤더 준수 후 재시도 |
| Gemini가 이미지 미반환 (텍스트만) | 프롬프트 강화 후 재시도 ×2 |
| 본문 제거 불완전 | 디자이너 검수 단계에서 플래그 — v1.5에서 자동 검증 |
| outpainting 인물 왜곡 | _meta.json에 기록, "재생성" UI로 처리 (v1.5) |
| 픽셀 스냅 실패 (입력 dimension 부족) | 명확한 에러 메시지로 사용자에 안내 |
| `OPENROUTER_API_KEY` 미설정 | 앱 시작 시 즉시 에러, 가이드 표시 |

---

## 7. `config.yaml` 스키마

```yaml
# 모델 카탈로그 (Streamlit 사이드바 드롭다운에 표시됨)
models:
  default: "google/gemini-2.5-flash-image"
  available:
    - id: "google/gemini-2.5-flash-image"
      label: "Gemini 2.5 Flash Image (Nano Banana)"
      cost_per_image: 0.04
    - id: "openai/gpt-image-1"
      label: "GPT-Image-1 (OpenAI)"
      cost_per_image: 0.19
    - id: "black-forest-labs/flux-1.1-pro"
      label: "FLUX 1.1 Pro (BFL)"
      cost_per_image: 0.04

# 텍스트 LLM (번역·언어감지)
text_llm: "openai/gpt-4o-mini"

# 출력 파일명 템플릿 (변수: {slug} {seq} {lang} {type} {variant} {date})
filename_template: "{seq:02d}_{lang}_{type}{variant_suffix}.png"
# 예: "01_kr_portrait.png", "09_kr_title_white.png"
# variant_suffix는 variant가 있을 때만 "_{variant}", 없으면 빈 문자열

# 출력 폴더 패턴
output_folder: "ready/{slug}/"

# 픽셀 스펙 (덮어쓰기 가능)
specs:
  portrait:  [900, 1600]
  landscape: [1600, 900]
  title:     [580, 200]
  banner:    [1520, 536]

# 모델별 프롬프트 override (선택)
prompts_override:
  "openai/gpt-image-1":
    clean: "prompts/clean_gpt.txt"   # 모델별 프롬프트 튜닝
```

---

## 8. v2 확장 hook

v1 코드에서 미리 준비할 인터페이스:

- `pipeline.process()`가 `input_path` 대신 `bytes`도 받을 수 있게 → Drive에서 다운로드한 bytes 직접 처리
- `save_outputs()` 함수를 분리 → v2에서 Drive 업로더로 교체 가능
- `_meta.json` 스키마에 `source: "local"|"sheet"` 필드 미리 둠 → v2에서 sheet row id 추적
- 모델 호출 함수를 `model_id` 파라미터화 → v2에서 사이드바 선택 그대로 전달
- 비밀값은 `os.getenv()` 추상화 → v2에서 Streamlit Secrets / 클라우드 secret manager로 교체 가능

---

## 9. 검증 계획 (코딩 시작 후)

1. **단위 동작 확인** (개발 중)
   - `gemini.clean()`이 본문 제거 결과를 반환하는지
   - `dims.snap()`이 정확히 1px 단위로 맞추는지
2. **End-to-end 첫 타이틀** (개발 완료 시)
   - 실제 CP 포스터 1장 입력 → 11개 출력 검증
   - 디자이너 검수 → 통과율·재작업 항목 기록
3. **5타이틀 파일럿** (출시 첫 주)
   - 통과율·비용·시간 측정 → KR 달성 여부 판단
   - 프롬프트·dimension 정책 튜닝

---

## 10. 해결된 Open Questions

| # | 질문 | 답 | 반영 |
|---|---|---|---|
| Q1 | 입력이 EN/CN portrait여도 KR portrait 새로 생성? | **YES** — 입력 언어와 무관하게 항상 clean → swap × 3 | STEP B+C, 부록 A |
| Q2 | CN 배너 타이틀 위치는? | **N/A** — 배너는 텍스트 없는 clean 버전 | STEP F (G 단계 삭제), prompts/outpaint_banner.txt |
| Q3 | Clean landscape는 텍스트 완전 0? | **YES** | prompts/clean.txt (유지) |
| Q4 | 타이틀 로고 색상은? | **원색 + 흰색 단색 둘 다** | 출력 #8~13 (3 lang × 2 color), STEP H 흰색 변환 |
| Q5 | 디자이너 검수 결과 기록 위치? | **미정** | v1.5 설계 시 결정 |
