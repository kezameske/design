# 포스터 자동화 시스템 v1

## 개요

ODK Media PMO팀이 CP로부터 받은 portrait 포스터 1장을 입력하면 다국어(KR/EN/CN) × 다오리엔테이션(portrait/landscape) × 타이틀 로고 등 총 **11종 변형**을 자동 생성하는 Streamlit 도구다. OpenRouter 경유 이미지 모델(기본 Gemini Flash Image 계열)과 PIL 후처리를 결합해 정확한 픽셀 스펙으로 출력한다.

- 디자이너용 사용 가이드: [WORKFLOW.md](./WORKFLOW.md)
- 기술 설계: [ARCHITECTURE.md](./ARCHITECTURE.md)
- 요구사항: [PRD-poster-automation.md](./PRD-poster-automation.md)

## 빠른 시작 (로컬)

1. 저장소 클론(또는 폴더 다운로드).
2. virtualenv 생성·활성화 후 의존성 설치:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. `.env` 설정:
   ```bash
   cp .env.example .env
   # .env 파일에 OPENROUTER_API_KEY 입력
   ```
4. 실행:
   ```bash
   streamlit run app.py
   ```
   브라우저가 자동으로 열린다 (보통 http://localhost:8501).

## 웹 배포 (Streamlit Community Cloud)

GitHub repo 연결 후 share.streamlit.io에서 배포한다. 설정:

- **Main file path**: `app.py` / **Branch**: `main`
- **Secrets** (앱 Settings → Secrets, TOML 형식):
  ```toml
  OPENROUTER_API_KEY = "sk-or-v1-..."
  APP_PASSWORD = "팀_공용_비밀번호"   # 선택 — 설정하면 접속 시 비밀번호 요구
  ```
- `runtime.txt`가 Python 버전을 고정한다.
- 클라우드 디스크는 휘발성 — `ready/` 출력은 앱 재시작 시 사라지므로 ZIP/개별 다운로드로 가져갈 것.

## 사용 흐름

포스터 업로드 → 3개 언어 타이틀 입력(또는 "AI로 나머지 채우기") → 스타일 메모(선택) → 생성 → 검수 → 다운로드.

생성은 두 방식:

- **단계별 검수 (권장)**: `1️⃣ 텍스트 제거 미리보기`(1회 호출)로 클린 결과를 먼저 확인하고 `✅ 승인하고 나머지 생성`(11회 호출). 포토샵으로 직접 지운 클린본을 업로드해 1단계를 생략할 수도 있다.
- **전체 생성 (검수 생략)**: 12회 호출 일괄 실행.

결과 그리드에서:
- 🔍 — 원본 크기 미리보기 + 원본/x2/x4 업스케일 다운로드
- 🔄 — 해당 출력만 재생성 (1회 호출; #7 재생성 시 4·5·6·11 재생성 권장)

## 출력

`ready/{slug}/` 폴더에 11개 PNG + `_meta.json` + `_work/`(재생성용 중간 산출물)가 생성된다.

| # | 파일 | 크기 |
|---|---|---|
| 1–3 | `kr/en/cn-portrait-title.png` | 900×1600 |
| 4–6 | `kr/en/cn-landscape-title.png` | 1600×900 |
| 7 | `clean-landscape-title.png` | 1600×900 (텍스트 없음) |
| 8–10 | `kr/en/cn-logo-title.png` | 580×200 (투명 배경) |
| 11 | `cn-main-banner-title.png` | 1520×536 (텍스트 없음) |

`_meta.json`에는 입력 정보, 출력 경로·dimension, API 호출 수, 추정 비용, 실패 단계, 재생성 횟수가 기록된다 (스키마는 ARCHITECTURE.md §5).

## 설정 커스터마이즈

`config.yaml`에서 변경할 수 있다:

- **기본 AI 모델 및 카탈로그** (`models.default`, `models.available`) — 사이드바 드롭다운에 노출됨. 이미지당 $0.10 초과 모델은 비용 통제를 위해 기본 비활성.
- **텍스트 LLM** (`text_llm`) — 언어 감지·번역에 사용 (기본 `google/gemini-2.5-flash`).
- **파일명 템플릿** (`filename_template`) — 변수: `{slug}`, `{seq}`, `{lang}`, `{type}`, `{variant}`, `{variant_suffix}`, `{date}`.
- **출력 폴더 패턴** (`output_folder`) — 기본 `ready/{slug}/`.
- **픽셀 스펙** (`specs`) — portrait/landscape/title/banner dimension 덮어쓰기.
- **모델별 프롬프트 override** (`prompts_override`) — 모델 id를 키로 단계별 프롬프트 파일 경로 지정.

프롬프트 자체는 `prompts/*.txt`가 기본값이며, UI의 "프롬프트 편집 (고급)"에서 실행 단위로 덮어쓸 수 있다.

## 테스트

```bash
pip install pytest
python3 -m pytest tests/ -q
```

API 호출은 모두 mock — 키 없이 실행 가능하다.

## 비용

- 이미지 호출 12회 × ~$0.05 + 텍스트 호출 3회 × $0.001 ≈ **타이틀당 약 $0.60** (기본 모델 기준)
- 단계별 검수 사용 시 클린 실패를 조기에 걸러 재실행 비용 절감
- 주당 10타이틀 = 약 $6/주
- PRD KR5 목표(타이틀당 $1 이하) 충족

## 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| `환경 변수 OPENROUTER_API_KEY가 설정되지 않았습니다` | `.env` 미생성 또는 키 미입력 (클라우드: Secrets 미설정) | `cp .env.example .env` 후 키 입력, 클라우드는 Settings → Secrets |
| 생성 버튼이 비활성 상태 | 원본 미업로드 또는 3개 타이틀 중 빈 칸 | 업로드 확인 + "AI로 나머지 채우기" 또는 수동 입력 |
| 자동 번역 실패 (403 등) | OpenRouter 텍스트 LLM 접근 불가 | `config.yaml`의 `text_llm`을 계정이 접근 가능한 모델로 변경 |
| 5xx / 429 등 OpenRouter 일시 오류 | 서버 일시 장애 또는 rate limit | 자동 재시도 ×3 후에도 실패 시 잠시 대기 후 재실행 (부분 결과 보관됨) |
| 출력이 11개 미만 | 일부 단계 실패 | 결과 화면 "실패 단계" 확인 후 해당 출력 🔄 재생성 |
| 본문 제거 불완전 / outpaint 왜곡 | 모델 품질 이슈 | 1단계 검수에서 재시도, 또는 해당 출력만 🔄 재생성 |
| 타이틀 로고가 불투명 / 보라 테두리 | 모델이 배경색을 벗어나게 그림 | 자동 보정 포함 — 남으면 해당 로고 🔄 재생성 |
| 개별 재생성 실패 (`_work/source.png이 없습니다`) | 구버전 실행 결과 | 전체 생성을 한 번 다시 실행 |
| `client error 413: ... 30MB` | OpenRouter 입력 30MB 한도 초과 | 2048px 정도로 다운스케일 후 업로드, 또는 아래 "고품질 원본 처리" |
| `streamlit: command not found` | virtualenv 미활성화 | `source .venv/bin/activate` 후 재실행 |

## 고품질 원본 처리 (선택)

OpenRouter 30MB 한도를 우회하려면 Google AI Studio 직접 백엔드를 활성화한다. `GOOGLE_API_KEY`를 `.env`(또는 클라우드 Secrets)에 추가하면 자동으로 전환되며, 18MB 초과 입력은 Google Files API로 업로드 후 처리한다.

```
GOOGLE_API_KEY=AIza...   # https://aistudio.google.com/app/apikey
```

키가 없으면 그대로 OpenRouter 사용 (기본).

## 로드맵

- **v1**: 로컬 Streamlit ✅
- **v1.5**: 단계별 검수 ✅ · 개별 출력 재생성 ✅ · 스타일 메모 ✅ · 웹 배포(Streamlit Cloud) + 공용 비밀번호 ✅ — 남은 항목: 비용·시간 대시보드, CSV 배치 모드
- **v2**: Google SSO, Google Sheets 입력, Google Drive 결과 업로드, 다중 타이틀 병렬 처리, AI 업스케일(Real-ESRGAN) 연동
