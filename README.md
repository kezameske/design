# 포스터 자동화 시스템 v1

## 개요

ODK Media PMO팀이 CP로부터 받은 portrait 포스터 1장을 입력하면 다국어(KR/EN/CN) × 다오리엔테이션(portrait/landscape) × 타이틀 로고(원색/흰색) 등 총 14종 변형을 자동 생성하는 로컬 Streamlit 도구다. Gemini 2.5 Flash Image(별칭 Nano Banana)와 PIL 후처리를 결합해 정확한 픽셀 스펙으로 출력한다. 자세한 요구사항·아키텍처는 [PRD-poster-automation.md](./PRD-poster-automation.md)와 [ARCHITECTURE.md](./ARCHITECTURE.md)를 참고하라.

## 빠른 시작

1. 저장소 클론(또는 폴더 다운로드).
2. virtualenv 생성·활성화 후 의존성 설치:
   ```bash
   cd /Users/jungholee/Desktop/Workspace/design
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

## 사용 흐름

포스터 업로드 → 3개 언어 타이틀 입력(또는 "AI로 나머지 채우기" 클릭) → "생성 시작" → 14개 출력 미리보기 → ZIP 다운로드 또는 `ready/<slug>/` 폴더에서 개별 접근.

- 3개 칸은 항상 표시되며 공식 라이선스 타이틀 보장을 위해 자유 수정 가능하다.
- 모든 칸이 채워지고 원본이 업로드된 상태에서만 생성 버튼이 활성화된다.
- 진행률은 `n/14 — 단계 설명` 형식으로 실시간 표시된다.

## 출력

`ready/{slug}/` 폴더에 14개 PNG + `_meta.json`이 생성된다.

| # | 파일 | 크기 |
|---|---|---|
| 1 | `01_kr_portrait.png` | 900×1600 |
| 2 | `02_en_portrait.png` | 900×1600 |
| 3 | `03_cn_portrait.png` | 900×1600 |
| 4 | `04_kr_landscape.png` | 1600×900 |
| 5 | `05_en_landscape.png` | 1600×900 |
| 6 | `06_cn_landscape.png` | 1600×900 |
| 7 | `07_all_landscape_clean.png` | 1600×900 (텍스트 없음) |
| 8 | `08_kr_title_color.png` | 580×200 (투명, 원색) |
| 9 | `09_kr_title_white.png` | 580×200 (투명, 흰색) |
| 10 | `10_en_title_color.png` | 580×200 (투명, 원색) |
| 11 | `11_en_title_white.png` | 580×200 (투명, 흰색) |
| 12 | `12_cn_title_color.png` | 580×200 (투명, 원색) |
| 13 | `13_cn_title_white.png` | 580×200 (투명, 흰색) |
| 14 | `14_cn_banner_clean.png` | 1520×536 (텍스트 없음) |

`_meta.json`에는 입력 정보, 출력 경로·dimension, API 호출 수, 재시도, 추정 비용, 실패 단계가 기록된다 (스키마는 ARCHITECTURE.md §5 참조).

## 설정 커스터마이즈

`config.yaml`에서 변경할 수 있다:

- **기본 AI 모델 및 카탈로그** (`models.default`, `models.available`) — 사이드바 드롭다운에 노출됨. Gemini 외 GPT-Image-1, FLUX 1.1 Pro 등 OpenRouter 경유 모델로 swap 가능.
- **텍스트 LLM** (`text_llm`) — 언어 감지·번역에 사용 (기본 `openai/gpt-4o-mini`).
- **파일명 템플릿** (`filename_template`) — 사용 가능 변수: `{slug}`, `{seq}`, `{lang}`, `{type}`, `{variant}`, `{variant_suffix}`, `{date}`.
- **출력 폴더 패턴** (`output_folder`) — 기본 `ready/{slug}/`.
- **픽셀 스펙** (`specs`) — portrait/landscape/title/banner dimension 덮어쓰기.
- **모델별 프롬프트 override** (`prompts_override`) — 모델 id를 키로 단계별 프롬프트 파일 경로 지정.

## 테스트

```bash
pip install pytest
pytest tests/ -v
```

End-to-end 테스트는 실제 `OPENROUTER_API_KEY`와 샘플 portrait 포스터가 필요하다.

## 비용

- 이미지 호출 12회 × $0.04 + 텍스트 호출 3회 × $0.001 ≈ **타이틀당 약 $0.48** (Gemini 2.5 Flash Image 기준)
- 주당 10타이틀 = 약 $4.8/주, 월 약 $19
- PRD KR5 목표(타이틀당 $1 이하) 충족

## 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| `환경 변수 OPENROUTER_API_KEY가 설정되지 않았습니다` | `.env` 미생성 또는 키 미입력 | `cp .env.example .env` 후 키 입력하고 `streamlit run` 재실행 |
| `❌ Input not found` | 입력 파일 경로 오류 | 업로드 다이얼로그에서 파일 재선택, 파일명에 특수문자 회피 |
| 생성 버튼이 비활성 상태 | 원본 미업로드 또는 3개 타이틀 중 빈 칸 존재 | 업로드 확인 + "AI로 나머지 채우기" 또는 수동 입력으로 3칸 모두 채우기 |
| 자동 번역 실패 | OpenRouter 텍스트 LLM 호출 오류 | 네트워크/키 확인 후 재시도, 또는 3개 칸 수동 입력 |
| 5xx / 429 등 OpenRouter 일시 오류 | 서버 일시 장애 또는 rate limit | 자동 재시도 ×3 후에도 실패 시 잠시 대기 후 재실행 (부분 결과는 보관됨) |
| 출력이 14개 미만 | 일부 단계 실패 (네트워크·모델 응답 오류) | 결과 화면의 "실패 단계" 섹션과 `_meta.json`의 `failures` 확인 후 재실행 |
| 본문 제거 불완전 / outpaint 인물 왜곡 | 모델 품질 이슈 | 디자이너 검수 후 해당 출력만 보정 (v1.5에서 개별 재생성 UI 제공 예정) |
| 픽셀 스냅 실패 | 입력 해상도가 타겟보다 작음 (최소 1080×1920 권장) | 더 큰 원본 요청 |
| `client error 413: Downloaded image content cannot exceed 30MB` | OpenRouter 입력 30MB 한도 초과 | 외부 도구로 미리 2048px 정도로 다운스케일 후 업로드. 또는 아래 "고품질 원본 처리" 참조 |
| `streamlit: command not found` | virtualenv 미활성화 | `source .venv/bin/activate` 후 재실행 |

## 고품질 원본 처리 (선택)

OpenRouter 30MB 한도를 우회하려면 Google AI Studio 직접 백엔드를 활성화한다. `GOOGLE_API_KEY`를 `.env`에 추가하면 자동으로 전환되며, 18MB 초과 입력은 Google Files API로 업로드 후 처리한다 (이론상 2GB까지 가능).

```
GOOGLE_API_KEY=AIza...   # https://aistudio.google.com/app/apikey
```

키가 없으면 그대로 OpenRouter 사용 (기본).

## 로드맵

- **v1**: 로컬 Streamlit (현재)
- **v1.5**: 개별 출력 재생성 UI, 디자이너 검수 플래그(통과/재생성/수동수정), 비용·시간 대시보드, CSV 배치 모드(CLI)
- **v2**: 웹 호스팅(Streamlit Cloud / Vercel / Cloud Run), Google SSO, Google Sheets 입력, Google Drive 결과 업로드, 다중 타이틀 병렬 처리
