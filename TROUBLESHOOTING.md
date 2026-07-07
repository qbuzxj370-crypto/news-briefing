# 트러블슈팅

운영 중 발생하는 문제와 해결법을 카탈로그 형식으로 정리. 같은 에러가 다시 났을 때 빠르게 참조하기 위함.

## 빠른 진단

에러 메시지 → 해당 섹션.

| 에러 메시지/증상 | 섹션 |
|---|---|
| `is a page, not a database` | [N1](#n1-database_id-is-a-page) |
| `Name is not a property that exists` | [N2](#n2-property-not-exists) |
| `본문 확보율 is not a property that exists` | [N2](#n2-property-not-exists) |
| `Could not find database with ID` | [N3](#n3-could-not-find-database) |
| `API token is invalid` (401) | [N4](#n4-token-invalid) |
| DB 만들었는데 속성이 `Name`/`이름` 하나뿐 | [N5](#n5-properties-not-auto-created) |
| DB 응답에 `properties` 키 없음 / `data_sources` 키 보임 | [N6](#n6-notion-api-2025-09-03) |
| 분야 0건 수집 | [R1](#r1-rss-url-dead) |
| UA 추가하니 일부 매체만 동작 | [R2](#r2-ua-blocking) |
| 수집 결과 날짜 비교 시 9시간 어긋남 | [R3](#r3-timezone-non-standard) |
| RSS에 기사 많은데 분야 0건 | [R4](#r4-date-range-empty) |
| `[robots] <host>: ...` 로그 + 특정 매체 본문 0건 | [R5](#r5-body-drop-by-robots) |
| `Unterminated string` JSON 파싱 실패 | [L1](#l1-llm-json-truncated) |
| `503 UNAVAILABLE` Gemini 응답 | [L2](#l2-gemini-overload) |
| `미해결(분류 실패)` 로그 / Slack `⚠️ 분류 미해결` | [L3](#l3-classification-unresolved) |
| Checkout 단계에서 잠시 fail 후 성공 | [O1](#o1-checkout-retry) |
| 로컬 수정사항이 Actions에 반영 안 됨 | [O2](#o2-push-skipped) |
| Slack 알림이 오지 않음 | [S1](#s1-slack-no-message) |
| `invalid_payload` / `403` Webhook 오류 | [S2](#s2-slack-webhook-error) |
| Slack 토픽 클릭해도 Notion 이동 안 됨 | [S3](#s3-slack-deeplink-broken) |

---

## Notion API/설정

### N1. database_id is a page

**증상**
```
APIErrorCode.ValidationError, message=Provided database_id <ID> is a page, not a database.
Use the pages API instead, or pass the ID of the database itself.
```

**원인**  
`NOTION_DATABASE_ID`에 부모 페이지 ID를 넣었거나, `init_db.py`를 안 돌리고 페이지 URL의 ID를 그대로 사용. database_id와 page_id는 다른 객체.

**해결**
1. `init_db.py`를 실행해서 DB 생성:
   ```bash
   export NOTION_API_KEY=secret_xxx
   export NOTION_PARENT_PAGE_ID=<부모페이지ID>
   python init_db.py
   ```
2. 출력된 `NOTION_DATABASE_ID` 값을 GitHub Secret에 등록.

**예방**  
GitHub Secret `NOTION_DATABASE_ID`에는 반드시 **DB ID**를 넣을 것. data_source_id나 page_id 아님.

**관련 결정**: [ADR-007 Notion DB row 방식](ADR.md#adr-007-notion-db-row-방식)

---

### N2. Property not exists

**증상**
```
APIErrorCode.ValidationError, message=Name is not a property that exists.
```
또는
```
본문 확보율 is not a property that exists.
```

**원인**  
코드가 요청하는 속성 이름과 DB의 실제 속성 이름이 다름. 한 글자, 띄어쓰기, 끝 공백 차이로도 발생.

**해결**
1. Notion에서 DB 풀 페이지로 열기.
2. 속성 헤더 더블클릭 → 코드와 동일한 이름인지 비교:
   - `날짜`, `모델`, `본문 확보율`, `분야 태그`, `분석 기사 수`
3. 다르면 정확히 일치하도록 수정.

복사용 정확한 이름:
```
날짜
모델
본문 확보율
분야 태그
분석 기사 수
```

**예방**  
수동으로 속성 추가 시 끝 공백 주의. 복붙 권장.

**관련 결정**: [ADR-007 Notion DB row 방식](ADR.md#adr-007-notion-db-row-방식)

---

### N3. Could not find database

**증상**
```
APIErrorCode.ObjectNotFound, message=Could not find database with ID: ***.
Make sure the relevant pages and databases are shared with your integration.
```

**원인**  
Integration이 해당 DB에 접근 권한 없음. 부모 페이지에 Integration을 추가해도 그 안의 DB에는 자동 전파되지 않는 경우가 있음. 특히 새로 만든 DB에서 자주 발생.

**해결**
1. Notion에서 DB를 **풀 페이지**로 열기 (우상단 화살표 아이콘).
2. 우상단 `⋯` (점 세 개) → `Connections` (또는 `통합 연결`).
3. 사용 중인 Integration(예: `news-briefing`)이 보이는지 확인.
4. 없으면 추가.

**예방**  
DB 새로 만들 때마다 Connections 확인. 부모 페이지 연결만으로는 부족.

**관련 결정**: [ADR-007 Notion DB row 방식](ADR.md#adr-007-notion-db-row-방식)

---

### N4. Token invalid

**증상**
```
APIErrorCode.Unauthorized, message=API token is invalid.
HTTP 401
```

**원인** (가능성 높은 순)
1. GitHub Secret 입력 시 앞뒤에 공백/줄바꿈 섞임.
2. Integration secret이 아닌 다른 값(Integration ID 등) 복사.
3. Integration이 삭제되거나 secret이 재발급됨.

**해결**
1. https://www.notion.so/profile/integrations 접속.
2. 해당 Integration → `Internal Integration Secret`의 `Show` → 복사 (`ntn_...` 또는 `secret_...` 시작).
3. GitHub 저장소 → Settings → Secrets and variables → Actions → `NOTION_API_KEY` `Update`.
4. 붙여넣을 때 끝에 줄바꿈 안 들어가도록 주의. 입력 후 `Update secret`.

**예방**  
Secret 갱신 시 클립보드에 줄바꿈이 들어가지 않는지 확인. macOS는 `Cmd+Shift+V`로 줄바꿈 제거 가능.

---

### N5. Properties not auto-created

**증상**  
`init_db.py` 실행했는데 DB에 `Name`(또는 `이름`) 속성 하나만 있음. 코드가 기대하는 `날짜`, `모델` 등이 없음.

**원인**  
`init_db.py`가 일부 실패했거나, Notion API의 자동 속성 생성 동작이 보장되지 않음. 특히 2025-09-03 신 API에서 동작이 약간 변경됨.

**해결**  
Notion UI에서 5개 속성 수동 추가:

| 속성 이름 | 타입 | 비고 |
|---|---|---|
| `날짜` | Date | |
| `모델` | Select | 옵션은 첫 페이지 생성 시 자동 추가됨 |
| `본문 확보율` | Number | "Show as"를 **Percent**로 설정 |
| `분야 태그` | Multi-select | 옵션은 첫 페이지 생성 시 자동 추가됨 |
| `분석 기사 수` | Number | |

**예방**  
DB 생성 후 항상 속성 5개 확인. 누락된 게 있으면 첫 실행 전 추가.

**관련 결정**: [ADR-007 Notion DB row 방식](ADR.md#adr-007-notion-db-row-방식)

---

### N6. Notion API 2025-09-03

**증상**
```
[경고] DB 응답에서 title 속성을 찾을 수 없음. 응답 키: ['object', 'id', 'title', 
'description', 'parent', ..., 'data_sources', ...]
```

**원인**  
Notion API 2025-09-03부터 데이터 모델 변경: 데이터베이스가 컨테이너가 되고, properties는 그 안의 data_source에 속함. 이전 API 스타일(`db["properties"]`)은 더 이상 작동 안 함.

**해결**  
`notion_writer.py`가 신 모델 처리하도록 작성됨. 핵심 변경:

```python
# 기존
db = client.databases.retrieve(database_id)
properties = db["properties"]  # 없음

# 신 모델
db = client.databases.retrieve(database_id)
data_source_id = db["data_sources"][0]["id"]
ds = client.request(path=f"data_sources/{data_source_id}", method="GET")
properties = ds["properties"]

# 페이지 생성 시
client.pages.create(
    parent={"type": "data_source_id", "data_source_id": data_source_id},
    ...
)
```

Client 초기화 시 버전 명시:
```python
client = Client(auth=token, notion_version="2025-09-03")
```

**예방**  
`notion-client` 라이브러리 업데이트 시 API 버전 변경사항 확인. 2025-09-03 이후 만든 DB는 모두 신 모델이라 반드시 data_source 경로 사용.

**관련 결정**: [ADR-007 Notion DB row 방식](ADR.md#adr-007-notion-db-row-방식)

---

## RSS 수집

### R1. RSS URL dead

**증상**
```
[IT·테크·AI] 0건 수집 (매체: set())
[경제·금융·증시] 0건 수집 (매체: set())
...
```

**원인**  
RSS URL이 죽었거나 변경됨. 매체가 RSS 서비스 자체를 폐지/이전한 경우 흔함.

**해결**  
`rss_probe.py`로 작동 매체 진단:
```bash
python rss_probe.py
```

출력 마지막에 작동 확인된 URL만 정리한 `RSS_FEEDS = {...}` 코드가 나옴. 이걸 `collectors.py`의 `RSS_FEEDS`에 복사.

후보가 부족하면 매체별 RSS 정책 직접 확인:
- 한국경제: `https://www.hankyung.com/feed/...`
- 매일경제: `https://www.mk.co.kr/rss/...`
- 한겨레: `https://www.hani.co.kr/rss/...`

**예방**  
월 1회 정도 `rss_probe.py` 실행해서 작동 상태 점검. 또는 운영 로그에서 "0건 수집"이 연속 발생하면 즉시 점검.

**관련 결정**: [ADR-005 5분야 + 매체 다양화](ADR.md#adr-005-5분야-구성--매체-다양화)

---

### R2. UA blocking

**증상**  
브라우저로는 RSS URL이 보이는데 `feedparser`로는 0건 반환.

**원인**  
일부 매체는 RSS 요청에 봇 차단(User-Agent 필터)을 적용. 기본 `feedparser` UA는 차단됨.

**해결**  
`collectors.py`의 `collect_articles`에서 UA 추가됨:
```python
parsed = feedparser.parse(feed_url, request_headers={
    "User-Agent": UA_DESKTOP,
})
```

**예방**  
신규 RSS 추가 시 UA 있는 상태로 진단.

**관련 결정**: [ADR-004 3단계 본문 크롤링 재시도](ADR.md#adr-004-3단계-본문-크롤링-재시도) (2/3단계에서 UA 활용)

---

### R3. Timezone non-standard

**증상**  
RSS 분야 0건. 그러나 probe로는 엔트리가 잡힘. 발행 시각을 보면 `+090` 같은 비표준 타임존.

**원인**  
한국 매체 일부가 RFC822 비표준 타임존(`+090`, 누락 등)을 보냄. feedparser는 `published_parsed`를 만들 때 타임존을 무시하고 시각 숫자만 넣음. 이걸 UTC로 간주하면 9시간 밀림.

**해결**  
`collectors._parse_published`가 KST 강제 부여:
```python
def _parse_published(entry):
    kst = timezone(timedelta(hours=9))
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            return datetime(*parsed[:6]).replace(tzinfo=kst)
    # 문자열 직접 파싱 폴백
    ...
```

**예방**  
한국 매체 RSS만 다루는 한 KST 가정이 합리적. 해외 매체 추가 시 재검토 필요.

---

### R4. Date range empty

**증상**  
RSS 엔트리는 있는데 수집 결과 0건. 로그에서 발행 시각이 수집 범위 밖.

**원인**  
초기 코드는 "어제 0시 ~ 오늘 0시 (KST)"로 좁게 잡음. 타임존 오차 + 수동 실행 시점에 따라 어제 기사가 범위 밖으로 빠짐.

**해결**  
`collectors.collect_articles`가 48시간 범위로 변경됨:
```python
start = now_kst - timedelta(hours=48)
# end는 현재 시각
```

**예방**  
범위 좁히고 싶으면 dedup이 중복 제거하니 무리하지 말 것. 좁은 범위 = 더 자주 0건.

### R5. Body drop by robots

**증상**  
특정 매체의 본문 확보가 0건으로 급락. 로그에 `[robots] <host>: HTTP 5xx — 본문 수집 생략` 또는 `[robots] <host>: 로드 실패(...)`. 심층 분석에 해당 매체가 요약만으로 등장하거나 빠짐.

**원인**  
본문 크롤링은 기사 페이지 요청 전 robots.txt를 확인한다 (ADR-018). 두 경우 본문을 건너뛴다:
1. 매체가 robots.txt로 해당 경로를 차단 (정책 변경) — 의도된 준수 동작.
2. robots.txt 응답이 5xx이거나 네트워크 오류 — 판단 불가라 보수적으로 차단. 일시적일 수 있음.

**해결**  
1. `python robots_probe.py`로 전 매체 판정 일괄 확인 (또는 `curl -A "Mozilla/5.0" https://<host>/robots.txt`로 규칙 직접 확인).
2. 일시 오류(5xx)면 다음 실행에서 자연 회복. 반복되면 매체 정책 변경 여부 확인.
3. 매체가 `User-agent: *`를 차단으로 바꿨다면 그 매체 본문은 포기 (요약 폴백으로 분석은 계속됨). 필요 시 피드 목록에서 대체 매체 검토.

**예방**  
robots 판정은 `tests/test_robots_gate.py`가 회귀 방지. 주의: `RobotFileParser.read()`로 되돌리지 말 것 — Python-urllib UA를 403으로 거부하는 매체(한겨레·매경 등)에서 전체 차단으로 오판한다. 반드시 requests + `UA_DESKTOP`으로 받아 `parse()`.

---

## LLM

### L1. LLM JSON truncated

**증상**
```
json.decoder.JSONDecodeError: Unterminated string starting at: line N column M
```

**원인**  
LLM이 JSON을 생성하다가 `max_tokens`에 도달해서 중간에 잘림. 입력이 크면 출력도 커지고, max_tokens가 부족하면 발생.

**해결**  
`analyzer.py`의 `max_tokens=32000`. Gemini 2.5 Flash 최대 65,536 토큰 한도 내.

여전히 부족하면:
1. `max_tokens`를 더 늘리기 (64000까지).
2. 입력 줄이기: `collectors.select_for_analysis`의 `max_total=8` → 6으로 축소.
3. 본문 길이 캡 줄이기: `analyzer._format_articles`의 `if len(content) > 2000` → 1500.

**예방**  
운영 로그에서 LLM 출력 길이 추적. 일관되게 max_tokens 80% 이상 사용하면 한도 부족 신호.

**관련 결정**: [ADR-001 LLM JSON 구조화 출력](ADR.md#adr-001-llm-json-구조화-출력--코드-위임-매핑--단일-호출) (JSON 잘림은 이 결정의 단점)

---

### L2. Gemini overload

**증상**
```
google.genai.errors.ServerError: 503 UNAVAILABLE.
'This model is currently experiencing high demand.'
```

**원인**  
Gemini 무료 티어 서버 일시 과부하. Google 측 문제, 우리 코드 무관.

**해결 (즉시)**  
잠시(5~30분) 후 GitHub Actions 수동 재실행.

**해결 (영구)**  
OpenAI API 키 발급 후 GitHub Secret `OPENAI_API_KEY` 등록. Gemini 실패 시 자동으로 GPT-5 mini 폴백 작동:

1. https://platform.openai.com/api-keys 에서 키 발급 (결제 등록 필요).
2. GitHub 저장소 → Settings → Secrets → New repository secret → `OPENAI_API_KEY`.
3. 다음 실행부터 `[LLM] 1차: gemini-2.5-flash, 폴백: gpt-5-mini` 로그 확인.

**예방**  
폴백 LLM 항상 설정. 무료 티어만으로 100% 가용성 보장 불가.

**관련 결정**: [ADR-009 Gemini Flash + OpenAI 폴백](ADR.md#adr-009-gemini-flash--openai-폴백)

---

### L3. Classification unresolved

**증상**
```
[경고] 미해결(분류 실패) 100/300건 — LLM 판정 없음, 분석 잠정 제외. 재실행 시 회복 대상 (ADR-017).
[경고] 분류 미해결 100건 — 이번 브리핑은 일부 분야가 누락됐을 수 있음. 재실행 시 회복됩니다.
```
또는 Slack 브리핑에 `⚠️ 분류 미해결 N건 — 일부 분야가 누락됐을 수 있습니다. 재실행하면 회복됩니다.`

**원인**  
분류는 100건 단위 배치로 처리(`CLASSIFY_BATCH_SIZE`). 한 배치의 LLM 호출/파싱이 실패하면(주로 Gemini 503·간헐 JSON 오류) 그 배치 ~100건이 **'미해결'**로 남는다. 배치 재시도(`BATCH_RETRY`)까지 실패한 잔여만 미해결로 처리되며, 그 런 분석에서 잠정 제외된다. genuine `기타`(LLM이 MISC로 판정)와 달리 **회복 대상**으로 구분 표시된다. 전 배치가 실패하면 전량 미해결 → 분석 대상 0건 → 빈 페이지(exit 1).

**해결 (즉시)**  
같은 날짜로 GitHub Actions 수동 재실행(`workflow_dispatch`). 스냅샷이 표본을 보존하므로, 재실행은 **미해결 부분집합만** 다시 분류해 회복한다(재수집 없음 → 표본 드리프트 없음). 로그에서 확인:
```
[2] 미해결 N건 회복 재분류 (부분집합)
```
회복이 끝나면 다음 재실행부터는 `[2] 스냅샷 완전(complete) — 회복 재분류 불필요`가 출력된다. Gemini 과부하가 원인이면 5~30분 뒤 재실행(L2와 동일).

**해결 (영구)**  
L2와 동일 — `OPENAI_API_KEY` 폴백 등록. Gemini 배치 실패 시 GPT-5 mini로 폴백돼 미해결 발생 자체가 줄어든다(단 폴백 분류는 기본 temperature).

**예방**  
- 폴백 키 등록(L2). 무료 티어만으로는 배치 실패가 간헐 발생.
- Slack `분류 미해결` 경고가 잦으면 Gemini 안정성·폴백을 점검. 회복은 재실행으로 자동이지만, 빈도가 높으면 근본 원인(과부하)을 손봐야 함.

**관련 결정**: [ADR-017 분류 부분실패 '미해결' 타입화 + 부분집합 회복](ADR.md#adr-017-분류-부분실패를-미해결-상태로-타입화--부분집합-회복)

---

## 운영/배포

### O1. Checkout retry

**증상**
```
Error: fatal: could not read Username for 'https://github.com'
Waiting 10 seconds before trying again
...
* [new ref]  ... -> origin/main
Checking out the ref
```

**원인**  
GitHub Actions runner의 인증 토큰 발급이 일시 지연됨. 빈번하진 않지만 가끔 발생.

**해결**  
**무시**. 자동 재시도(3회)가 있어 결국 성공. 마지막에 `Checking out the ref` + commit hash 출력되면 정상.

다음 단계로 이미 넘어갔으면 신경 안 써도 됨.

---

### O2. Push skipped

**증상**  
로컬에서 코드 수정 → GitHub Actions 재실행 → 옛 동작이 그대로.

**원인**  
`git commit`은 했지만 `git push`를 안 함. 또는 push가 실패했는데 인지 못 함.

**해결**
```bash
git log --oneline -5  # 최근 커밋 확인
git status            # "up to date with origin/main" 확인
git push              # 미푸시 커밋 있으면 푸시
```

GitHub Actions 실행 로그 맨 위 `Checkout` 단계에서 받은 commit hash가 로컬 최신 commit과 동일한지 확인.

**예방**  
커밋 후 항상 push까지 확인. `git status`로 "Your branch is up to date" 메시지 확인 습관.

---

## Slack 알림

### S1. Slack no message

**증상**  
Actions가 green으로 끝났는데 Slack DM/채널에 알림이 없음.

**원인**  
`SLACK_WEBHOOK_URL` Secret이 미등록이거나 빈 값. 이 경우 `slack_writer` 호출 자체를 건너뜀 (정상 동작 — Secret 미설정 = Slack 비활성).

**해결**
1. GitHub 저장소 → Settings → Secrets and variables → Actions → `SLACK_WEBHOOK_URL` 확인.
2. 없으면: Slack API(https://api.slack.com/apps) → 앱 생성 → Incoming Webhooks 활성화 → Webhook URL 복사 → Secret 등록.
3. Actions에서 `workflow_dispatch`로 수동 실행 후 `[7] Slack 알림` 로그 라인이 출력되는지 확인. 없으면 Secret 미등록 상태.

---

### S2. Slack webhook error

**증상**
```
Slack POST 실패: 403
Slack POST 실패: invalid_payload
```
(Actions 실행 로그에서 확인)

**원인**
- **403**: Webhook URL이 폐기(revoked)됐거나 앱이 채널에서 제거됨.
- **invalid_payload**: Block Kit JSON 구조가 Slack API 규격을 벗어남. 보통 텍스트가 너무 길거나 특수 문자 이슈.

**해결 (403)**
1. Slack API → 앱 → Incoming Webhooks → 새 Webhook URL 발급.
2. GitHub Secret `SLACK_WEBHOOK_URL` 업데이트.

**해결 (invalid_payload)**  
`slack_writer._briefing_blocks`에서 `section` 블록의 text 길이가 3000자를 초과하면 Slack이 거부한다(현재 코드는 2800자 캡). TL;DR나 이슈 제목에 Slack 예약 문자(`<`, `>`, `&`)가 있으면 `&lt;` 등으로 이스케이프 필요.

---

### S3. Slack deeplink broken

**증상**  
Slack 메시지의 이슈 토픽 버튼을 클릭했을 때 Notion 앱에서 페이지 찾을 수 없음 또는 잘못된 위치로 이동.

**원인 및 해결**

| 원인 | 확인 방법 | 해결 |
|---|---|---|
| `_fetch_issue_block_urls` 실패 (Notion API 오류) | Actions 로그에서 `딥링크 수집 실패` 경고 확인 | Notion API 키 유효성 재확인, 일시 오류면 재실행 |
| deep_issue 제목이 `📊` 접두어 없이 렌더링됨 | Notion 페이지 열어서 h3 헤딩 텍스트 확인 | renderer.py 템플릿에서 `📊` 접두어 복구 |
| Notion URL 포맷 변경 | URL을 직접 복사해서 열어보기 | `_fetch_issue_block_urls`의 URL 포맷(`/page_id#block_id`) 업데이트 |
| 앱 아닌 웹브라우저로 열림 | 클릭 후 주소창 확인 | Notion 앱 설치 후 기본 앱으로 설정 |

딥링크가 없으면 Slack 메시지 최하단 "노션에서 전문 보기" 링크로 페이지 전체를 열고 수동으로 이동.

---

## 운영 모니터링 가이드

### 매일 점검 (5분)
- DB에 오늘 날짜 페이지가 생성됐는지
- TL;DR 콜아웃이 그날 핵심을 담고 있는지
- deep_issues 출처 링크가 실제로 열리는지
- 본문 확보율(DB 속성)이 평균 70% 이상인지

### 주간 점검
- 분야별 기사 수 균형 (산업 분야가 자주 10건 미만이면 매체 추가 고려)
- 모델 사용 분포 (gpt-5-mini 폴백이 자주 작동하면 Gemini 안정성 점검)
- 본문 확보율 추세 (하락하면 R1/R2 점검)

### 월간 점검
- `python rss_probe.py` 실행해서 RSS 작동 상태 검증
- DB 페이지 누적 개수와 매일 자동 생성 누락 여부 (캘린더뷰로 빠진 날짜 확인)

### 이상 신호 (즉시 점검 필요)
- 연속 2일 이상 "수집 0건" → RSS URL 점검 (R1)
- 본문 확보율 50% 미만 며칠 → 본문 크롤링 셀렉터 조정 필요
- Gemini 503이 주 3회 이상 → 폴백 키 등록 (L2)
- Slack `분류 미해결` 경고가 잦음 → 재실행으로 회복되나, 빈번하면 폴백 키 등록·Gemini 안정성 점검 (L3)
- DB 페이지가 정상 생성되는데 분야 태그가 매일 1~2개뿐 → 일부 RSS 죽음 신호 (R1 부분 발생)
