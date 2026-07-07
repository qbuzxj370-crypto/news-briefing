# Daily News Briefing

매일 아침 한국 주요 매체 RSS를 수집·분석하여 Notion DB에 자동 보고서를 게시하고 Slack으로 알림을 발송.

## 아키텍처

```
RSS 수집 (9개 매체 11개 피드, 평탄화 — 분야 구분 없이 한 풀)
   ↓
LLM 분야 분류 (제목 기반·temp=0, 5분야 + 기타 — ADR-012)
   · 배치 실패는 '미해결'로 분리·재시도, 재실행 시 부분집합만 회복 — ADR-017
   ↓ 분야별 유사 제목 dedup
선별 후보만 본문 크롤링 (robots.txt 준수·3단계 재시도, 목표 도달 시 중단 — ADR-018)
   ↓ 하이브리드 선별 (본문 우선)
Gemini 2.5 Flash JSON 분석 (실패 시 GPT-5 mini 폴백)
   ↓ 코드가 article_id → 매체명/링크 자동 매핑
Jinja2 마크다운 렌더링
   ↓ 토글 마커 + 콜아웃 마커
Notion 블록 변환 + DB row 생성 (실패 시 로컬 백업)
   ↓ deep_issue 헤딩 블록 ID 수집 (Slack 딥링크용)
Slack Incoming Webhook 알림 (SLACK_WEBHOOK_URL 설정 시)
```

## 수집 매체

분야는 피드 단위가 아니라 LLM이 제목 기반으로 사후 분류한다(ADR-012 —
한국 매체 RSS는 종합 피드 누수가 심해 피드→분야 매핑이 부정확).
피드 목록의 정본은 `collectors.RSS_FEEDS`.

| 매체 | 피드 섹션 |
|---|---|
| IT조선 | 전체 기사 |
| 전자신문 | 속보(Section902) |
| 머니투데이 | 종합 |
| 한국경제 | 경제 |
| 매일경제 | 경제 |
| 동아일보 | 정치, 사회 |
| 경향신문 | 정치, 사회 |
| 오마이뉴스 | 정치 |
| 한겨레 | 사회 ※ RSS에 발행시각이 없어 기사 페이지에서 날짜 추출 (`_date_from_page`) |

## 수집 정책

개인 열람 목적의 도구이며, 매체 부담과 저작권 노출을 최소화하도록 설계했다 (ADR-018):

- **기사 목록은 매체가 공식 제공하는 RSS 피드**에서만 가져온다.
- **본문은 분석에 쓰이는 선별 후보에 한해** 기사 페이지에서 수집하며(분야당 8~20건, 목표 도달 시 중단), 페이지 요청 전 **해당 매체의 robots.txt를 확인**해 차단된 URL은 요청하지 않는다. 요청 간 지연을 둔다.
- **산출물(Notion/Slack)에 기사 본문을 재게시하지 않는다** — 보고서는 LLM 분석문 + 기사 제목 + 원문 링크만 포함한다.
- **저장·백업에도 본문을 남기지 않는다** (ADR-019) — 재실행용 스냅샷(→ Actions 아티팩트)에는 제목·요약(RSS 제공분)·링크·분야만 기록하고, 본문은 메모리에서 LLM 분석에만 쓰고 버린다.
- 이 저장소를 포크해 운영하는 경우, 수집 대상 매체의 이용약관 준수 책임은 운영자에게 있다.

## 페이지 구조

- 📰 제목 + 날짜
- 💡 TL;DR 콜아웃 (2~3줄 핵심)
- 🌐 메가 트렌드 + 분야 횡단 흐름
- 분야 5개:
  - 🎯 핵심 흐름 (3줄 윤곽)
  - 📊 심층 이슈 토글 (펼치면 맥락/함의/관전 포인트 + 관련 기사)
  - 📌 그 외 이슈 토글 (간단 분석 + 인라인 출처)
  - 💡 전문가 인사이트
- 수집 매체 목록 푸터

## 초기 설정

요구사항: **Python 3.11** (Actions 워크플로와 동일 버전 — `.github/workflows/*.yml`의 `python-version`).

### 1. Notion Integration 생성

1. [Notion Integration](https://www.notion.so/my-integrations)에서 새 통합 생성
2. "Internal Integration Secret" 복사 → `NOTION_API_KEY`
3. Notion에서 부모 페이지 생성 후, 페이지 상단 `⋯` → `Connections` → 위 통합 추가
4. 페이지 URL에서 ID 추출 (32자 hex 부분) → `NOTION_PARENT_PAGE_ID`

### 2. DB 생성

```bash
pip install -r requirements.txt

export NOTION_API_KEY=secret_xxx
export NOTION_PARENT_PAGE_ID=xxxxxxxxxxxxxxxx

python init_db.py
```

출력된 `NOTION_DATABASE_ID`를 저장.

> **이미 브리핑 DB가 있는 경우** (재설치·저장소 이전): `init_db.py`를 다시 실행하지
> 말 것 — 기존 DB와 무관한 **빈 DB가 새로 생긴다**. 기존 DB ID는 Notion에서 해당
> DB를 전체 페이지로 열고 `⋯` → `Copy link` → URL의 `?v=` 앞 32자 hex.
> 값이 맞는지는 아래 4번의 사전 검증(databases 조회)으로 확인.

### 3. API 키 발급

- **Gemini API 키** (필수): [Google AI Studio](https://aistudio.google.com/apikey)에서 발급. 무료 티어.
- **OpenAI API 키** (선택, 폴백용): [OpenAI Platform](https://platform.openai.com/api-keys). GPT-5 mini 사용. 미설정 시 Gemini 실패하면 알림 페이지만 생성.

### 4. GitHub Actions 설정

**등록 전에 각 값을 로컬에서 검증**하면 등록 후 삽질을 예방할 수 있다.
복사 과정에서 앞뒤 공백·줄바꿈이 섞이면 401이 난다 ([N4](TROUBLESHOOTING.md#n4-token-invalid)):

```bash
# GEMINI_API_KEY — 모델 목록 JSON이 나오면 유효
curl -s "https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY" | head -3

# OPENAI_API_KEY (선택) — 모델 목록 JSON이 나오면 유효
curl -s https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY" | head -3

# NOTION_API_KEY — 통합(bot) 정보가 나오면 유효
curl -s https://api.notion.com/v1/users/me \
  -H "Authorization: Bearer $NOTION_API_KEY" -H "Notion-Version: 2025-09-03"

# NOTION_DATABASE_ID — 200이면 ① 진짜 DB ID이고 ② 통합이 DB에 연결된 상태.
# "Could not find database" → 통합 미연결(N3) 또는 페이지 ID를 넣음(N1)
curl -s "https://api.notion.com/v1/databases/$NOTION_DATABASE_ID" \
  -H "Authorization: Bearer $NOTION_API_KEY" -H "Notion-Version: 2025-09-03" | head -3

# SLACK_WEBHOOK_URL (선택) — 응답이 "ok"면 유효. 채널에 테스트 메시지가 실제 발송됨
curl -s -X POST -H 'Content-type: application/json' \
  --data '{"text":"webhook 검증"}' "$SLACK_WEBHOOK_URL"
```

검증이 끝나면 저장소 `Settings` → `Secrets and variables` → `Actions`에서 추가:

| Secret | 필수 | 설명 |
|---|---|---|
| `GEMINI_API_KEY` | ✓ | Google AI Studio에서 발급 |
| `OPENAI_API_KEY` | 선택 | GPT-5 mini 폴백용 |
| `NOTION_API_KEY` | ✓ | Notion Integration Secret |
| `NOTION_DATABASE_ID` | ✓ | `init_db.py` 출력값 (기존 DB 재사용 시 위 2번 참고) |
| `SLACK_WEBHOOK_URL` | 선택 | Slack 알림용 Incoming Webhook URL |

워크플로우는 매일 KST 07:00에 자동 실행.

- **실행 시각을 바꾸려면 두 곳을 함께 수정**: `daily.yml`의 `cron`(UTC)과
  `timewindow.py`의 `SCHEDULED_HOUR_KST`(KST). 하나만 바꾸면 수집 윈도우가 어긋난다.
- **포크로 시작했다면** `Actions` 탭에서 워크플로 활성화가 먼저 필요하다 (포크는 기본 비활성).
- 저장소에 60일간 활동이 없으면 GitHub가 schedule 실행을 자동 중지한다 — `Actions` 탭 배너에서 재활성화.

### 5. Slack Incoming Webhook 설정 (선택)

`SLACK_WEBHOOK_URL`을 설정하면 매일 브리핑 완료 후 Slack으로 알림이 발송됩니다.

1. [Slack API](https://api.slack.com/apps) → `Create New App` → `From scratch`
2. `Incoming Webhooks` 활성화 → `Add New Webhook to Workspace`
3. 알림 받을 채널(또는 DM) 선택 → Webhook URL 복사
4. 복사한 URL을 `SLACK_WEBHOOK_URL` Secret으로 등록

**Slack 알림 내용:**

```
📰 2026-06-23 데일리 브리핑

💡 오늘의 핵심
미·중 반도체 규제 강화로 삼성·SK 수출 타격 우려.

💻 IT·테크·AI
  • 📊 삼성전자 HBM4 양산 본격화     ← 클릭 시 Notion 해당 블록으로 이동
  • 📊 AI 기본법 시행령 초안 공개

💰 경제·금융·증시
  • 📊 원/달러 1,380원 돌파

[노션에서 전문 보기 →]
```

오류 발생 시에도 원인과 함께 실패 알림을 발송합니다.

### 6. 첫 실행 검증

Secrets 등록 후 다음 날 아침을 기다리지 말고 바로 확인:

1. `Actions` 탭 → `Daily News Briefing` → `Run workflow` (수동 실행)
2. 로그에서 `[1] RSS 수집` → `[2] LLM 분야 분류` → … 단계가 순서대로 찍히는지 확인
3. Notion DB에 새 브리핑 페이지가 생겼는지 확인 (Slack 설정 시 알림 도착 확인)

실패하면 로그의 에러 메시지로 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) 빠른 진단 표에서 찾는다.

## 로컬 실행

```bash
export GEMINI_API_KEY=...
export OPENAI_API_KEY=...        # 선택
export NOTION_API_KEY=...
export NOTION_DATABASE_ID=...
export SLACK_WEBHOOK_URL=...     # 선택

python main.py
```

## 종료 코드

| 코드 | 의미 |
|---|---|
| 0 | 정상 완료 |
| 1 | 분석 대상 0건 — 수집 0건(RSS 피드 점검 필요) 또는 분류 전면 실패(전량 미해결, 재실행 시 회복) |
| 2 | LLM 분석 실패 (Gemini + OpenAI 양쪽 다 실패) |
| 3 | Notion 업로드 실패 (로컬 백업은 `backups/`에 보존) |

GitHub Actions 실행 시 `backups/` 폴더는 항상 아티팩트로 업로드되므로 30일간 다운로드 가능. 아티팩트에 기사 본문은 포함되지 않는다 (스냅샷은 제목·링크·분야만 — ADR-019).

## 비용 추정

- **Gemini 2.5 Flash**: 무료 티어 (일일 호출 한도 내).
- **GPT-5 mini 폴백**: 호출당 약 0.005~0.02 USD. 월 5회 폴백 가정 시 약 200원.
- **Notion API**: 무료.
- **GitHub Actions**: Public 저장소는 무료. Private도 월 2,000분 무료 (회당 약 2~3분이라 여유).

**총 예상 비용**: 월 0~수백 원 수준.

## 구성

```
news-briefing/
├── main.py                    # 파이프라인 오케스트레이션 (진입점)
├── collectors.py              # RSS 수집 + 본문 크롤링 + dedup
├── classifier.py              # LLM 분야 분류 (5분야 + 기타)
├── llm.py                     # Gemini/OpenAI 어댑터 + 폴백 래퍼
├── analyzer.py                # JSON 스키마 + LLM 분석 + 검증
├── renderer.py                # JSON → 마크다운 (Jinja2)
├── notion_writer.py           # 마크다운 → Notion 블록 + 업로드 + 딥링크 수집
├── slack_writer.py            # Slack Incoming Webhook 알림 발송
├── timewindow.py              # 수집 윈도우 계산 (cron 예정 시각 기준 고정)
├── snapshot.py                # 수집·분류 스냅샷 (재실행 시 표본 보존 + 미해결 분류 회복)
├── init_db.py                 # DB 최초 생성 스크립트
├── rss_probe.py               # RSS URL 작동 진단 (운영 중 점검용)
├── robots_probe.py            # robots.txt 판정 진단 (ADR-018, R5 점검용)
├── dup_probe.py               # 날짜 간 중복 기사 빈도 측정 (ADR-015)
├── templates/
│   └── report.md.j2           # Jinja2 마크다운 템플릿
├── tests/                     # 회귀 테스트 (run_tests.sh로 일괄 실행)
├── run_tests.sh               # 테스트 일괄 실행 스크립트
├── README.md                  # 본 문서
├── PLAN.md                    # 잔여 작업 계획 (우선순위·완료 기준)
├── initial_design.md          # 초기 설계 문서 (아카이브)
├── page_mockup.md             # 페이지 구조 모형
├── sample_output.md           # 산출물 샘플
├── TROUBLESHOOTING.md         # 운영 중 문제 카탈로그
├── ADR.md                     # 주요 설계 결정 기록
├── requirements.txt
└── .github/workflows/
    ├── daily.yml              # 매일 KST 07:00 브리핑 실행
    └── test.yml               # push마다 전체 테스트 실행
```

## 테스트

```bash
bash run_tests.sh                  # 전체 일괄 실행
python tests/test_timewindow.py    # 개별 실행
```

push 시 GitHub Actions(`test.yml`)가 자동 실행한다. 테스트는 운영 결함의
회귀 방지 중심 — 각 파일 docstring에 결함 배경이 기록되어 있다.

## 출처 무결성

LLM은 분석에만 집중하고 매체명·링크는 코드가 결정론적으로 매핑한다.

- LLM 출력은 `referenced_article_ids: [3, 7, 12]` 같은 인덱스 배열만 포함
- `collectors.SOURCE_MAP` (도메인 → 매체명)과 `Article.link`가 100% 정확한 출처 보장
- LLM이 매체명·URL을 만들어내는 환각 가능성 원천 차단

## 문서

운영 중 참고할 문서들:

- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — 발생한 문제와 해결법 카탈로그. 에러 메시지 → 빠른 진단 표로 시작.
- **[ADR.md](ADR.md)** — 주요 설계 결정의 맥락과 근거. "왜 이렇게 만들었는지" 답하는 문서.
- **[page_mockup.md](page_mockup.md)** — Notion 페이지 구조 모형.
- **[initial_design.md](initial_design.md)** — 운영 전 작성된 초기 설계 문서 (아카이브). 의사결정 흐름의 역사 기록.

## 트러블슈팅 (요약)

자세한 내용은 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) 참고. 자주 발생하는 패턴:

- **수집 0건**: RSS URL이 죽었을 가능성. `python rss_probe.py`로 진단 → [TROUBLESHOOTING R1](TROUBLESHOOTING.md#r1-rss-url-dead)
- **Notion 업로드 실패** (`Could not find database`): Integration이 DB에 미연결 → [N3](TROUBLESHOOTING.md#n3-could-not-find-database)
- **속성 없음** (`is not a property that exists`): DB 속성 이름 불일치 → [N2](TROUBLESHOOTING.md#n2-property-not-exists)
- **Gemini JSON 잘림** (`Unterminated string`): `max_tokens` 부족 → [L1](TROUBLESHOOTING.md#l1-llm-json-truncated)
- **Gemini 503**: 일시 과부하. 폴백 등록 권장 → [L2](TROUBLESHOOTING.md#l2-gemini-overload)
- **분류 미해결** (`미해결(분류 실패)` 로그 / Slack `⚠️ 분류 미해결`): 배치 부분실패. 같은 날짜 재실행 시 미해결 부분집합만 자동 회복 → [L3](TROUBLESHOOTING.md#l3-classification-unresolved)
