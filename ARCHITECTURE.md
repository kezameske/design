# Architecture — OTT 포스터 자동화 시스템 v1

PRD: [PRD-poster-automation.md](./PRD-poster-automation.md)

이 문서는 개발자용 기술 설계서다. 파일 구조·데이터 흐름·모듈 책임·Gemini 프롬프트 초안·에러 처리 전략을 다룬다.

---

## 1. 파일 구조

```
design/
├── PRD-poster-automation.md     # PRD (요구사항 스냅샷)
├── ARCHITECTURE.md              # 이 문서
├── WORKFLOW.md                  # 디자이너용 사용 가이드
├── README.md                    # 설치·실행·배포 가이드
├── requirements.txt             # 의존성
├── runtime.txt                  # Streamlit Cloud Python 버전 고정
├── .env.example                 # OPENROUTER_API_KEY / GOOGLE_API_KEY / APP_PASSWORD 예시
├── config.yaml                  # 모델 목록, 파일명 템플릿, 스펙, 프롬프트 override
│
├── app.py                       # Streamlit UI (비밀번호 게이트, 단계별 검수, 재생성)
├── pipeline.py                  # 11-output 오케스트레이션 + stage_clean + regenerate
├── gemini.py                    # 이미지 API 래퍼 (OpenRouter / Google direct)
├── translate.py                 # 언어 감지 + 번역
├── dims.py                      # PIL 픽셀 스냅 + 크로마키 유틸
│
├── prompts/                     # 프롬프트 템플릿 (source of truth)
│   ├── clean.txt
│   ├── outpaint_landscape.txt
│   ├── outpaint_banner.txt
│   ├── title_swap.txt
│   └── title_extract.txt
│
├── tests/                       # pytest (API 전부 mock)
├── inputs/                      # 사용자 업로드 임시 저장
└── ready/
    └── <slug>/                  # 타이틀당 출력 폴더 (11개)
        ├── kr-portrait-title.png      # 900x1600   (#1)
        ├── en-portrait-title.png      #            (#2)
        ├── cn-portrait-title.png      #            (#3)
        ├── kr-landscape-title.png     # 1600x900   (#4)
        ├── en-landscape-title.png     #            (#5)
        ├── cn-landscape-title.png     #            (#6)
        ├── clean-landscape-title.png  # 텍스트 없음 (#7)
        ├── kr-logo-title.png          # 580x200 투명 (#8)
        ├── en-logo-title.png          #            (#9)
        ├── cn-logo-title.png          #            (#10)
        ├── cn-main-banner-title.png   # 1520x536, 텍스트 없음 (#11)
        ├── _meta.json
        └── _work/                     # 재생성용 중간 산출물
            ├── source.png             # 업로드 원본
            └── clean_portrait.png     # STEP B 결과
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
        │                               │  → 출력 #11       │
        │                               │  (텍스트 합성 없음)│
        │                               └──────────────────┘
        ▼
┌─────────────────────────────────────────────────┐
│ STEP G. 타이틀 로고 추출 × 3                      │
│   for lang in {kr, en, zh}:                      │
│     raw = gemini.title_extract(                  │
│             portraits[lang], ref=source)         │
│     logo = dims.chroma_key_magenta(              │
│             raw, fallback_auto=True)             │
│       ※ #FF00FF 배경 → 투명. 모델이 핑크로       │
│         드리프트해도 테두리색 자동 감지로 키잉     │
│   → 출력 #8, #9, #10                             │
└─────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────┐
│ STEP I. 픽셀 스냅 (모든 출력 11개)                │
│   for each output:                               │
│     dims.snap(image, target_dim)                 │
│   → 정확한 픽셀 dimension 보장                    │
└─────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────┐
│ STEP J. 저장 + _meta.json + _work/ 기록          │
└─────────────────────────────────────────────────┘
```

(배너는 STEP F에서 출력 **#11**, 흰색 로고 변형은 v1에서 제외 — 출력은 총 11개.)

**총 Gemini 호출:** 1(clean) + 3(portrait swap) + 1(landscape outpaint) + 3(landscape swap) + 1(banner outpaint) + 3(title extract) = **12회**
(단계별 검수 흐름에서 승인된 클린본을 `precleaned`로 넘기면 STEP B 생략 → 11회)

**총 LLM 호출:** 1(감지) + 2(번역) = **3회**

**총 PIL 처리:** 3(크로마키) + 11(픽셀 스냅) = 14회 (모두 로컬, 무료)

### 단계별 실행 진입점 (v1.5)

```
stage_clean(source)            # STEP B만 — UI의 "텍스트 제거 미리보기"
process(..., precleaned=...)   # STEP B 생략하고 C~J 실행 — "승인하고 나머지 생성"
regenerate(out_dir, seq)       # 출력 1개만 재실행 — 결과 그리드의 🔄 버튼
```

`regenerate()`는 `_work/`의 중간 산출물과 디스크의 상위 출력 파일을 소스로 쓴다:

| seq | 소스 | API 호출 |
|---|---|---|
| 1–3 | `_work/source.png` | title_swap |
| 7 | `_work/clean_portrait.png` | outpaint(landscape) |
| 4–6 | 출력 #7 파일 (+ source 참조) | title_swap |
| 11 | 출력 #7 파일 | outpaint(banner) |
| 8–10 | 출력 #1/2/3 파일 (+ source 참조) | title_extract + 크로마키 |

※ #7 재생성은 4·5·6·11로 자동 전파되지 않음 — UI가 재생성 권장 안내를 표시.

---

## 3. 모듈 책임

### `gemini.py`

이미지 모델 호출 래퍼. 기본 백엔드는 OpenRouter, `GOOGLE_API_KEY`가 설정되면 Google AI Studio 직접 호출로 자동 전환 (18MB 초과 입력은 Files API 경유).

```python
def clean(image_bytes, *, model_id=None, prompt_override=None) -> bytes:
    """포스터에서 본문·타이틀 모두 제거. clean base 반환."""

def outpaint(image_bytes, target: Literal["landscape", "banner"], *,
             model_id=None, prompt_override=None) -> bytes:
    """비율 변환. landscape=16:9, banner=1520:536 비율 outpainting."""

def title_swap(clean_base, title, lang, *, model_id=None, prompt_override=None,
               reference_bytes=None, style_notes=None) -> bytes:
    """타이틀 교체/합성. reference_bytes로 원본 타이포그래피 참조,
    style_notes로 디자이너 지시사항 추가."""

def title_extract(poster, title, lang, *, model_id=None, prompt_override=None,
                  reference_bytes=None, style_notes=None) -> bytes:
    """타이틀만 #FF00FF 마젠타 배경으로 분리 (downstream에서 크로마키)."""
```

**중요 구현 디테일:**
- 모든 함수가 **타겟보다 큰 사이즈로 생성 요청** → dims.py가 정확 픽셀로 스냅
- 호출 실패 시 자동 재시도 ×3 (exponential backoff)
- 응답 파싱 실패(이미지 미반환) 시도 retry 대상
- `style_notes`는 포맷된 프롬프트 끝에 "Designer notes" 섹션으로 추가됨

### `translate.py`

OpenRouter 경유 텍스트 LLM (`config.yaml::text_llm`, 기본 `google/gemini-2.5-flash`).

```python
def detect(title: str) -> Literal["kr", "en", "zh"]:
    """타이틀 문자열에서 언어 추정."""

def translate(title: str, src: str, targets: list[str]) -> dict[str, str]:
    """src 언어 → targets 언어들로 번역. {lang: translated} 반환."""
```

**프롬프트 전략:** 단순 번역이 아닌 "OTT 콘텐츠 타이틀 현지화" 컨텍스트 제공 (의역 허용, 글자 수 비슷하게 유지 등).

### `dims.py`

PIL 기반 정확한 픽셀 스냅 + 크로마키.

```python
def snap(image_bytes: bytes, target: tuple[int, int],
         mode: Literal["crop", "resize"] = "crop") -> bytes:
    """
    - mode='crop': 타겟 비율로 center-crop → Lanczos resize
    - mode='resize': 비율 무시하고 직접 resize (로고 등 알파 PNG용)
    """

def chroma_key_magenta(png_bytes, key_color=(255, 0, 255), tolerance=30,
                       fallback_auto=False) -> bytes:
    """
    마젠타 배경 → 투명 변환 (title_extract 후처리).
    - tolerance 이내(Chebyshev) → alpha 0, 2×tolerance까지 소프트 램프
      (안티앨리어싱 가장자리 fringe 완화)
    - fallback_auto=True: #FF00FF 키잉이 30% 미만이면 테두리 중앙값 색을
      감지해 재키잉 (모델이 배경을 핑크로 드리프트하는 케이스 대응)
    """

def to_white(rgba_png_bytes: bytes) -> bytes:
    """알파 유지하며 RGB를 흰색으로 치환. v1 출력에는 미사용 (흰색 로고
    변형이 스펙에서 제외됨) — 향후 변형 추가용으로 유지."""

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

### `pipeline.py`

오케스트레이션. STEP B~J 실행 (STEP A 번역은 UI가 선처리).

```python
def process(input_path: Path | bytes | str,
            titles: dict[str, str],          # {"kr","en","zh"} — 모두 필수
            model_id: str | None = None,     # 사이드바 드롭다운 override
            progress_cb: Callable[[int, int, str], None] | None = None,
            slug: str | None = None,
            prompt_overrides: dict[str, str] | None = None,  # UI 프롬프트 편집기
            precleaned: bytes | None = None, # 승인된 클린본 → STEP B 생략
            style_notes: str | None = None,  # 디자이너 지시 → swap/extract에 추가
           ) -> dict:
    """Returns {"slug": ..., "outputs": {1: Path, ...}, "meta": {...}}"""

def stage_clean(input_path, model_id=None, prompt_override=None) -> bytes:
    """STEP B만 실행 — 단계별 검수 UI의 미리보기용. 1회 호출."""

def regenerate(out_dir, seq, model_id=None, prompt_overrides=None,
               style_notes=None) -> Path:
    """출력 1개만 재실행. titles/model/style_notes는 _meta.json에서 복원,
    소스는 _work/ 중간 산출물 + 디스크의 상위 출력 파일."""
```

**참고:** 언어 키는 내부적으로 `kr/en/zh` (translate.py 호환)이고, 파일명 라벨은 `kr/en/cn` (사용자 spec). 매핑은 `_OUTPUT_SPECS` 테이블이 담당.

**병렬화:** Phase C(포트레이트 합성 ×3)와 Phase D(landscape outpaint)는 독립 → `ThreadPoolExecutor`로 병렬. Phase E/F/G/H 도 가능한 곳 병렬화.

**Retry 정책:**
- 각 Gemini 호출당 ×3
- 전체 step 실패 시 부분 결과 보관 + `_meta.json`에 실패 step 기록
- UI에서 "실패한 출력만 재실행" 가능

### `app.py`

Streamlit UI. 주요 구성 (위에서 아래 순):

1. **비밀번호 게이트** — `APP_PASSWORD` env/secret이 설정된 경우만 활성. 세션 단위 인증.
2. **secrets 브릿지** — 클라우드의 `st.secrets`를 `os.environ`으로 복사 (모듈은 env만 읽음).
3. **사이드바** — 모델 드롭다운 + 단가/타이틀당 비용 표시.
4. **입력** — 원본 업로드, 타이틀 3칸, "✨ AI로 나머지 채우기"(translate), 스타일 메모, 프롬프트 편집기(expander).
5. **단계별 검수** — `1️⃣ 텍스트 제거 미리보기` → `stage_clean()` → 결과 표시 + 재시도/승인 버튼. 클린본 직접 업로드 expander로 STEP B 생략 가능. 새 원본 업로드 시 미리보기 자동 무효화.
6. **실행** — `전체 생성` 또는 `승인하고 나머지 생성` → `pipeline.process(precleaned=...)`, progress_cb로 11단계 진행률 표시.
7. **결과 그리드** — 출력별 🔍(미리보기 dialog: 원본/x2/x4 다운로드) + 🔄(`pipeline.regenerate()`). ZIP 다운로드, 실패 단계 expander, 비용 요약.

Streamlit 위젯 제약 대응 패턴: 위젯이 session_state 키를 점유한 뒤에는 쓰기 금지 → "AI로 나머지 채우기"와 프롬프트 초기화는 플래그를 세우고 `st.rerun()` 후 위젯 생성 전에 처리하는 2-phase 방식.

---

## 4. Gemini 프롬프트 초안

**Source of truth는 `prompts/*.txt` 파일이다.** 이 문서에는 초안을 복붙하지 않는다 (운영 중 계속 튜닝되어 문서가 금방 낡기 때문). 현재 적용된 주요 전략만 요약:

| 프롬프트 | 핵심 전략 |
|---|---|
| `clean.txt` | 텍스트 전부(타이틀 포함) 제거, 아트워크 보존 |
| `outpaint_landscape.txt` | 좌우 자연 확장 + **하단 1/3을 타이틀용 여백으로 비우는 구도** (피사체 약간 위로) |
| `outpaint_banner.txt` | 초광폭 확장, 텍스트 0 + **피사체 우측 배치, 좌측 40% 텍스트용 여백** |
| `title_swap.txt` | 기존 타이틀 교체 패턴. 두 번째 이미지(원본)를 타이포그래피 레퍼런스로 사용 — 언어 간 색/재질 드리프트 방지 |
| `title_extract.txt` | 타이틀만 **#FF00FF 마젠타 배경**에 렌더 → dims.py 크로마키. 정확한 #FF00FF 강제 (핑크 드리프트 방지 지시 포함) |

공통 메커니즘:
- `{title}`, `{language}` 플레이스홀더는 gemini.py가 `.format()`으로 치환
- UI "프롬프트 편집 (고급)"의 내용이 `prompt_overrides`로 전달되어 실행 단위 덮어쓰기
- `config.yaml::prompts_override`로 모델별 프롬프트 파일 교체 가능
- 스타일 메모(`style_notes`)는 swap/extract 프롬프트 끝에 "Designer notes" 섹션으로 추가

---

## 5. `_meta.json` 스키마

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
    "style_notes": "금박 질감 유지"
  },
  "model": {
    "image_model": "google/gemini-3.1-flash-image-preview",
    "cost_per_image_usd": 0.05
  },
  "outputs": {
    "1": {"path": "ready/lets_play_soccer_3/kr-portrait-title.png",
          "dim": [900, 1600], "lang": "kr", "type": "portrait",
          "variant": "", "status": "ok"},
    "...": "...  (1~11, 실패 시 path=null + status='failed')"
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

- `regenerations`는 `regenerate()` 호출 시마다 +1 (개별 재생성 횟수 추적)
- `regenerate()`는 titles/model/style_notes를 이 파일에서 복원하므로, `_meta.json`이 없으면 개별 재생성 불가

---

## 6. 에러 처리 전략

| 상황 | 처리 |
|---|---|
| OpenRouter 일시 오류 (5xx) | exponential backoff 재시도 ×3 |
| Rate limit (429) | `Retry-After` 헤더 준수 후 재시도 |
| Gemini가 이미지 미반환 (텍스트만) | 프롬프트 강화 후 재시도 ×2 |
| 본문 제거 불완전 | 단계별 검수 UI에서 재시도, 또는 클린본 직접 업로드 |
| outpainting 인물 왜곡 | 결과 그리드 🔄 버튼으로 해당 출력만 재생성 |
| 타이틀 로고 배경이 #FF00FF에서 드리프트 | `chroma_key_magenta(fallback_auto=True)`가 테두리색 자동 감지로 키잉 |
| 픽셀 스냅 실패 (입력 dimension 부족) | 명확한 에러 메시지로 사용자에 안내 |
| `OPENROUTER_API_KEY` 미설정 | 앱 시작 시 즉시 에러, 가이드 표시 |

---

## 7. `config.yaml` 스키마

**현재 값은 [config.yaml](./config.yaml)이 source of truth.** 스키마 요약:

```yaml
# 모델 카탈로그 (사이드바 드롭다운). 이미지당 $0.10 초과 모델은 비용 통제로
# 기본 비활성 (config 주석에 목록 유지).
models:
  default: "google/gemini-3.1-flash-image-preview"
  available:
    - id: "<openrouter-model-id>"
      label: "<드롭다운 표시명>"
      cost_per_image: 0.05        # 추정 단가 — UI 비용 표시·meta 기록에 사용

# 텍스트 LLM (번역·언어감지). 계정이 접근 가능한 모델이어야 함 (403 주의).
text_llm: "google/gemini-2.5-flash"

# 출력 파일명 템플릿 (변수: {slug} {seq} {lang} {type} {variant} {variant_suffix} {date})
# lang ∈ kr|en|cn|clean, type ∈ portrait|landscape|logo|main-banner
filename_template: "{lang}-{type}-title.png"
# 예: "kr-portrait-title.png", "clean-landscape-title.png"

# 출력 폴더 패턴
output_folder: "ready/{slug}/"

# 픽셀 스펙 (덮어쓰기 가능 — 변경 시 전체 파이프라인이 해당 크기로 출력)
specs:
  portrait:  [900, 1600]
  landscape: [1600, 900]
  title:     [580, 200]
  banner:    [1520, 536]

# 모델별 프롬프트 override (선택)
prompts_override: {}
```

---

## 8. v2 확장 hook

v1 코드에서 미리 준비할 인터페이스:

- `pipeline.process()`가 `input_path` 대신 `bytes`도 받을 수 있게 → Drive에서 다운로드한 bytes 직접 처리
- `save_outputs()` 함수를 분리 → v2에서 Drive 업로더로 교체 가능
- `_meta.json` 스키마에 `source: "local"|"sheet"` 필드 미리 둠 → v2에서 sheet row id 추적
- 모델 호출 함수를 `model_id` 파라미터화 → v2에서 사이드바 선택 그대로 전달
- ~~비밀값은 `os.getenv()` 추상화~~ → ✅ 적용됨: app.py가 `st.secrets` → `os.environ` 브릿지 (Streamlit Cloud 배포에서 사용 중)

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
| Q4 | 타이틀 로고 색상은? | ~~원색 + 흰색~~ → **원색만** (스펙 확정 시 흰색 변형 제외) | 출력 #8~10, `dims.to_white()`는 미사용으로 유지 |
| Q5 | 디자이너 검수 결과 기록 위치? | **미정** | v1.5 설계 시 결정 |
