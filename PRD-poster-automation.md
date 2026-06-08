# PRD — OTT 포스터 자동화 시스템 v1

## 1. Summary

ODK Media PMO팀이 매주 약 10개 신규 OTT 타이틀당 14종 포스터 변형(다국어 portrait/landscape, clean 베이스, 타이틀 로고 원색·흰색, CN clean 메인 배너)을 수동 제작하는 현행 프로세스를 자동화한다. CP가 제공한 portrait 원본 1장을 입력하면 AI 이미지 편집(Gemini 2.5 Flash Image) + PIL 후처리 파이프라인이 정확한 픽셀 스펙으로 14종을 자동 생성한다. v1은 로컬 Streamlit 웹 UI로 트리거하며, v2에서 Google Sheets 연동으로 확장한다.

---

## 2. Contacts

| 이름 | 역할 | 비고 |
|---|---|---|
| PMO팀 (pmo-team@odkmedia.net) | Product Owner | 요구사항 정의, v1 검증, 운영 |
| 개발 담당 (사용자) | Engineer | v1 구현, v2 자동화 인프라 |
| 디자인팀 | Stakeholder | 출력 품질 검수, 재작업 피드백 |
| 콘텐츠팀 | Stakeholder | CP 원본 수령·정리 |
| 운영팀 | Stakeholder | 다국가 플랫폼 송출 |

---

## 3. Background

**컨텍스트.** ODK Media는 한국 OTT 콘텐츠를 글로벌(미국·중국·동남아 등) 다국가 플랫폼에 배급한다. CP(콘텐츠 프로바이더)가 신규 타이틀 포스터를 portrait 1장(언어는 KR/EN/CN 중 임의)으로 전달하며, PMO/디자인팀은 이를 송출 채널별 스펙에 맞춰 다국어·다오리엔테이션·다용도로 11종 변형해 납품한다.

**현황.**
- 주당 약 10개 신규 타이틀 → 140장/주 수동 작업
- 타이틀당 평균 2시간 디자이너 공수 발생
- 송출 플랫폼 픽셀 스펙 오류 시 반려·재작업
- CN 메인 배너(1520×536) 등 비표준 비율은 매번 수작업 합성

**왜 지금인가.** 2025년 출시된 Gemini 2.5 Flash Image(별칭 "Nano Banana")가 포스터 본문 제거·outpainting(비율 변환)·다국어 타이틀 합성을 단일 모델로 수행 가능하며, 호출당 약 $0.04로 경제성도 확보됐다. 동시에 OpenRouter 등 통합 API가 보편화되어 모델 swap·과금 통합이 쉬워졌다. 1년 전까지는 비용·품질·다국어 텍스트 렌더링 어느 하나가 부족해 자동화가 어려웠다.

---

## 4. Objective

**목적.** 포스터 변형 제작을 수동에서 자동으로 전환하여 (1) PMO/디자인팀 공수를 절감하고 (2) 신규 타이틀 출시 리드타임을 단축한다.

**전략 정렬.** 글로벌 동시 송출 전략의 병목 중 하나인 "현지화 산출물 준비"를 압축하여, 콘텐츠 출시 → 시장 노출 사이 지연을 줄인다.

**Key Results (SMART).**

| KR | 측정 지표 | 목표 | 기한 |
|---|---|---|---|
| KR1 | 타이틀당 14종 생산 소요 시간 | 수동 평균 2시간 → 10분 미만 | v1 출시 후 3개월 |
| KR2 | 누적 처리 타이틀 수 | 40+ | 운영 첫 4주 |
| KR3 | 디자이너 재작업률 (생성물 중 수정·재생성 비율) | 30% 이하 | 운영 4주차 측정 |
| KR4 | 출력 픽셀 스펙 정확도 | 100% (모든 출력이 지정 dimension과 1px 오차 없이 일치) | v1 출시 시점 |
| KR5 | 타이틀당 API 비용 | $1 이하 | 지속 |

---

## 5. Market Segment(s)

**Primary 사용자.** ODK Media 내부 PMO·디자인·콘텐츠 운영 담당자 (10~15명).

**Job-to-be-done.**
> "신규 OTT 타이틀이 도착하면 다국가 송출 가능 상태로 가능한 빨리, 누락 없이 준비한다."

**사용자가 처한 제약.**
- **입력 제약**: CP가 portrait만 제공, 언어는 KR/EN/CN 중 1개로 매번 다름
- **출력 제약**: 송출 플랫폼별 정확한 픽셀 스펙 필수 (1px 오차도 반려 사유)
- **품질 제약**: 한국어·영어·중국어 타이포그래피가 모두 OTT 송출 기준 충족
- **사용성 제약**: 비기술자(PMO·디자이너)가 셀프로 사용 가능해야 함 — 개발자 개입 없이
- **시간 제약**: 신규 타이틀 도착 후 빠르면 당일 송출 필요

---

## 6. Value Proposition(s)

**해소되는 Pain.**
| Pain | 영향 |
|---|---|
| 11종 수작업 = 타이틀당 ~2시간 | 디자이너 시간 낭비, 출시 지연 |
| 다국어 텍스트 깨짐·잘림 발생 | 송출 반려, 재작업 |
| 비표준 비율(1520×536 등) 매번 수작업 | 디자이너 의존, 속도 저하 |
| 픽셀 스펙 오류 → 플랫폼 반려 | 운영팀-디자이너 핑퐁 |

**제공되는 Gain.**
- 디자이너는 **최종 검수·품질 보정**에만 집중 → 창의적 작업 비중↑
- 출시 리드타임 단축 → **시장 노출 속도** 향상
- 추가 언어·플랫폼 확장 시 한계비용 ~0

**경쟁 대안 대비 차별점.**
| 대안 | 한계 |
|---|---|
| 외주 디자인 | 비용↑, 리드타임 며칠 |
| Photoshop 액션·템플릿 | 다국어 텍스트 자동화 한계, 비율 변환 불가 |
| 범용 Canva/Figma 자동화 | OTT 포스터 outpainting 품질 부족 |
| 본 시스템 | 단일 AI 모델 + 코드 후처리로 시간·비용 동시 우위 |

---

## 7. Solution

### 7.1 UX / 사용자 플로우

**v1 사용 흐름 (로컬 Streamlit).**

```
[PMO 담당자]
    1. 로컬에서 `streamlit run app.py` 실행
    2. 브라우저 자동 열림 → 업로드 UI
    3. portrait 포스터 1장 드래그&드롭
    4. 3개 타이틀 입력칸 (Korean / English / Chinese)이 모두 표시
       - 사용자가 어느 칸이든 1개 이상 입력
       - "✨ AI로 나머지 채우기" 버튼 클릭 시 비어있는 칸을 AI가 번역해 채움
       - 모든 칸은 자유 수정 가능 (공식 라이선스 타이틀 보장 위해)
    5. "생성 시작" 클릭 (3개 칸 모두 채워져야 활성화)
    6. 진행 상황 표시 (예: "4/14 — landscape EN 생성 중…")
    7. 완료 시 14종 썸네일 프리뷰
    8. "ZIP 다운로드" 또는 `ready/<slug>/` 폴더에서 개별 접근
```

**입력 UI 레이아웃:**

```
┌── 원본 업로드 ────────────┐
│ 📁 portrait.jpg          │
└───────────────────────────┘

🇰🇷 Korean Title    [뭉쳐야찬다3              ]
🇬🇧 English Title   [Let's Play Soccer 3      ]
🇨🇳 Chinese Title   [一起踢足球3              ]

[ ✨ AI로 나머지 채우기 ]   [ 생성 시작 ]
```

**중요:** OTT 콘텐츠는 공식 라이선스 타이틀이 정해진 경우가 많아 AI 자동 번역만으로는 부정확할 수 있다. 모든 입력칸을 항상 표시하고 사용자가 직접 수정·확정 후 생성을 시작하도록 한다.

**예외 처리.**
- API 오류 → 자동 재시도 3회 → 실패 시 어떤 단계 실패했는지 표시 + 부분 결과 보관
- 출력 품질 의심 시 디자이너 검수 → 개별 출력만 "재생성" 가능 (전체 재실행 불필요)

### 7.2 Key Features

| ID | 기능 | 설명 | 우선순위 |
|---|---|---|---|
| F1 | 언어 자동 감지 | 입력 타이틀이 KR/EN/CN 중 어느 언어인지 LLM 판별 | Must |
| F2 | 타이틀 번역 | 입력 언어 → 나머지 2개 언어 자동 번역, 수동 override 가능 | Must |
| F3 | 본문 텍스트 제거 | Gemini로 포스터의 본문·카피·기존 타이틀 모두 제거하여 clean base 생성 | Must |
| F4 | 오리엔테이션 변환 | Portrait clean base → Landscape outpainting | Must |
| F5 | 배너 비율 변환 | Landscape clean base → 1520×536 ultra-wide CN clean 배너 outpainting (텍스트 없음) | Must |
| F6 | 타이틀 합성 | clean base × 언어별 타이틀 텍스트 → 포스터 6종 (portrait + landscape × 3 lang) | Must |
| F7 | 타이틀 로고 추출 | 포스터에서 타이틀만 투명 PNG로 분리 (580×200) × 3언어 (원색) | Must |
| F8 | 타이틀 로고 흰색 변환 | F7 출력의 알파 채널 유지하며 RGB를 흰색으로 치환 → 흰색 로고 3종 | Must |
| F9 | 정확한 픽셀 스냅 | 모든 출력을 PIL로 center-crop + Lanczos resize → 정확한 spec dimension | Must |
| F10 | 진행 상황 표시 | Streamlit UI에서 각 단계별 진행률·실패/재시도 표시 | Must |
| F11 | 메타 기록 | 타이틀당 `_meta.json` (입력 정보·소요 토큰·비용·재시도·오류) | Should |
| F12 | 개별 출력 재생성 | 14개 중 특정 출력만 다시 만들기 | Should |
| F13 | CSV 배치 모드 | CLI로 여러 타이틀 일괄 처리 | Could |
| F14 | AI 모델 선택 | Streamlit 사이드바 드롭다운으로 매 실행 시 모델 선택 (Gemini 2.5 Flash Image 기본, OpenRouter 경유 다른 모델로 swap 가능) | Must |
| F15 | 파일명 템플릿 | `config.yaml`에 출력 파일명 템플릿 정의. 변수: `{slug}` `{seq}` `{lang}` `{type}` `{variant}` `{date}` | Must |

### 7.3 Technology

| 영역 | 선택 | 비고 |
|---|---|---|
| Language | Python 3.10+ | 표준 |
| Image generation | Gemini 2.5 Flash Image (Nano Banana) via OpenRouter | `google/gemini-2.5-flash-image`, ~$0.04/image |
| Text LLM (번역·감지) | OpenRouter 경유 저비용 모델 (예: gpt-4o-mini) | ~$0.001/call |
| Image post-processing | Pillow (PIL) | center-crop + Lanczos resize |
| Local UI | Streamlit | 비기술자도 셀프 운영 가능 |
| HTTP | requests | stdlib만으로는 multipart 까다로움 |
| 패키징 | requirements.txt | virtualenv 권장 |
| 설정 | `config.yaml` | 파일명 템플릿, 기본 모델, 모델별 프롬프트 override 등 |
| 인증 | OPENROUTER_API_KEY (환경변수 또는 .env) | v2에서 Google 서비스 계정 + 사용자 인증 추가 |

### 7.4 Assumptions

명시적으로 검증해야 할 가설:

| ID | 가설 | 검증 방법 |
|---|---|---|
| A1 | Gemini 2.5 Flash Image이 한/영/중 타이포그래피를 OTT 송출 기준으로 생성 가능 | v1 첫 5타이틀 디자이너 검수 |
| A2 | Portrait → Landscape outpainting 첫 시도 통과율 70%+ | 첫 10타이틀 통과율 측정 |
| A3 | CP 원본 해상도가 충분히 큼 (최소 1080×1920) | 과거 자료 샘플링 |
| A4 | 1520×536 CN 메인 배너 비율이 주요 CN 플랫폼(아이치이·텐센트 등)에서 공통 사용 가능 | 운영팀 확인 |
| A5 | OpenRouter API 응답 시간이 타이틀당 5분 이내 | 첫 운영 측정 |
| A6 | PMO/디자이너가 1회 가이드로 Streamlit 로컬 실행 가능 | 온보딩 세션 |
| A7 | 베트남어·태국어는 v1 범위에서 제외 가능 (CN까지만 필수) | 비즈니스 요구 확인 |

---

## 8. Release

### v1 — Core 자동화 (목표: 약 2주)

- 로컬 Streamlit UI
- 11종 자동 생성 파이프라인
- 로컬 파일 입출력 (`inputs/`, `ready/<slug>/`)
- 자동 retry × 3
- 메타 JSON 기록

### v1.5 — 품질·운영 가드레일 (목표: 약 1개월)

- 개별 출력 재생성 UI
- 디자이너 검수 플래그 (통과/재생성/수동수정)
- 비용·시간 대시보드
- CSV 배치 모드 (CLI)

### v2 — 웹 호스팅 + Google 연동 (목표: 약 2~3개월)

- **웹 호스팅** (Streamlit Cloud / Vercel / Cloud Run 중 택1)
- 사용자 인증 (Google SSO 또는 도메인 화이트리스트 `@odkmedia.net`)
- API 비용 가드 (사용자별/일별 한도)
- Google Sheets 입력 (Drive URL + 타이틀 컬럼)
- Google Drive 결과 업로드 (로컬 디스크 휘발성 대응)
- 시트 기반 큐 polling 또는 Apps Script 트리거
- 다중 타이틀 병렬 처리
- 백그라운드 작업 처리 (HTTP 타임아웃 회피)

### v3 — 확장 (장기)

- 베트남어·태국어 등 언어 추가
- 모델별 자동 swap (예: CN 텍스트 품질 미달 시 GPT-Image-1 자동 라우팅)
- 플랫폼별 스펙 프리셋 (Netflix / Tving / iQIYI 등)
- 디자이너 코멘트 → 자동 재프롬프트 학습

---

## 부록 A. 출력 스펙 매트릭스 (v1)

| # | 출력 | Dimension | 배경 | 비고 |
|---|---|---|---|---|
| 1 | KR portrait | 900×1600 | 원본 베이스 | clean base + KR 타이틀 합성 (입력 언어와 무관하게 항상 새로 생성) |
| 2 | EN portrait | 900×1600 | 원본 베이스 | clean base + EN 타이틀 합성 |
| 3 | CN portrait | 900×1600 | 원본 베이스 | clean base + CN 타이틀 합성 |
| 4 | KR landscape | 1600×900 | outpainted | clean landscape + KR 타이틀 합성 |
| 5 | EN landscape | 1600×900 | outpainted | clean landscape + EN 타이틀 합성 |
| 6 | CN landscape | 1600×900 | outpainted | clean landscape + CN 타이틀 합성 |
| 7 | Clean landscape | 1600×900 | outpainted | **텍스트 없음** (썸네일·웹 헤더용) |
| 8 | KR title logo (color) | 580×200 | transparent | 알파 PNG, 원본 색상 |
| 9 | KR title logo (white) | 580×200 | transparent | 알파 PNG, 흰색 단색 (PIL 변환) |
| 10 | EN title logo (color) | 580×200 | transparent | 알파 PNG, 원본 색상 |
| 11 | EN title logo (white) | 580×200 | transparent | 알파 PNG, 흰색 단색 |
| 12 | CN title logo (color) | 580×200 | transparent | 알파 PNG, 원본 색상 |
| 13 | CN title logo (white) | 580×200 | transparent | 알파 PNG, 흰색 단색 |
| 14 | CN main banner (clean) | 1520×536 | outpainted | **텍스트 없음**, CN 플랫폼 hero용 |

## 부록 B. 비용 추정

- 이미지 호출 (Gemini): 12회/타이틀
  - clean portrait 1
  - portrait title swap × 3
  - landscape outpaint 1
  - landscape title swap × 3
  - banner outpaint 1 (텍스트 합성 없음)
  - title extract × 3
- 흰색 로고 변환: PIL 로컬 처리, **API 호출 없음**
- 이미지 호출 비용: 12 × $0.04 = **~$0.48**
- 텍스트 호출: 언어 감지 1 + 번역 2 × $0.001 = ~$0.003
- **타이틀당 약 $0.48** (목표 $1 이하 충족)
- 주당 10타이틀 = **~$4.8/주**, 월 **~$19**
