# 포스터 자동화 워크플로우

한글 OTT 포스터 1장 → **한/영 포스터 2종 + 한/영 타이틀 추출본 2종** 자동 생성.

## 0. 사전 준비 (필수)

`OPENROUTER_API_KEY`가 **실제 실행 셸**에 노출돼 있어야 한다.

> ⚠️ 주의: 터미널 세션에만 export해두면, 다른 도구/비대화형 셸에서 실행할 때
> 키를 상속받지 못해 `❌ Set OPENROUTER_API_KEY env var`로 Step 1에서 실패한다.

키 전달 방법 (택1):

```bash
# 1) 현재 셸에 export (가장 확실)
export OPENROUTER_API_KEY="sk-or-..."

# 2) 명령에 인라인으로 전달 (1회용)
OPENROUTER_API_KEY="sk-or-..." python3 run_pipeline.py ...

# 3) --api-key 옵션으로 전달
python3 run_pipeline.py ... --api-key "sk-or-..."
```

확인:
```bash
[ -n "$OPENROUTER_API_KEY" ] && echo "키 있음" || echo "키 없음"
```

## 1. 실행

```bash
python3 run_pipeline.py <입력파일> "<한글타이틀>" "<영문타이틀>"
```

예시:
```bash
python3 run_pipeline.py variety1.jpg "뭉쳐야찬다3" "Let's Play Soccer3"
python3 run_pipeline.py poster3.jpg "태어난김에 세계일주4" "Adventure by Accident 4"
```

옵션:
- `--name <base>` — 출력 파일 베이스 이름 (기본: 입력 파일명에서 추출)
- `--out <dir>` — 출력 폴더 (기본: `./ready/`)
- `--skip-clean` — 입력이 이미 본문 제거된 경우 Step 1 생략
- `--skip-titles` — 타이틀 추출 생략 (포스터 2종만 생성)
- `--api-key <key>` — OPENROUTER_API_KEY 대신 키 직접 전달

## 2. 4단계 파이프라인

| 단계 | 스크립트 | 역할 |
|---|---|---|
| Step 1 | `gemini_clean.py` | 포스터 본문 텍스트 제거 (타이틀은 유지) |
| Step 2 | `openrouter_title_swap.py` | 한글 타이틀 → 영문 변환 |
| Step 3·4 | `gemini_title_extract.py` | 한/영 타이틀을 투명 배경 PNG로 추출 |

## 3. 출력

`ready/` 폴더에 PNG 4개:
- `<base>_ko.png` — 한글 포스터
- `<base>_en.png` — 영문 포스터
- `title_<base>_ko.png` — 한글 타이틀 (투명 PNG)
- `title_<base>_en.png` — 영문 타이틀 (투명 PNG)

## 4. 비용

- **모델**: Gemini 2.5 Flash Image (via OpenRouter)
- **비용**: 약 **$0.16 / 장**

## 5. 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| `❌ Set OPENROUTER_API_KEY env var` (Step 1 exit 1) | 실행 셸에 키 미노출 | 위 0번 방법으로 키 전달 |
| `❌ Input not found` | 입력 파일 경로 오류 | 파일명/경로 확인 |
| `❌ Required script not found` | 스크립트 파일 누락 | 폴더 내 4개 .py 파일 존재 확인 |
