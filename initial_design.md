# Daily News Briefing — 초기 설계 문서 (Archived)

> ⚠️ **이 문서는 운영 전 작성된 초기 설계 문서다. 실제 운영 시스템의 모습과 일부 다르다.**
>
> 운영 중 변경된 주요 결정:
> - 출처/링크: 인라인 표기 강제 → **코드 위임 매핑** (LLM은 article_id만 반환)
> - LLM 출력: 마크다운 직접 생성 → **JSON 구조화 출력 + Jinja2 렌더링**
> - 분야 수: 4분야 → **5분야** (정치/사회 분리)
> - 환경변수: `NOTION_PARENT_PAGE_ID` → `NOTION_DATABASE_ID` (init_db.py로 DB 생성)
> - 모듈 구조: renderer.py, init_db.py, rss_probe.py, templates/ 추가
> - 페이지 구조: TL;DR 콜아웃 + 토글 헤더 + minor 토글 등 신설
>
> **현재 운영 시스템 정보는 다음 문서를 참조:**
> - [README.md](README.md) — 시스템 개요, 설치/운영 가이드
> - [ADR.md](ADR.md) — 결정 변경 이력 (왜 바뀌었는지)
> - [page_mockup.md](page_mockup.md) — 최종 페이지 구조 모형
> - [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — 운영 중 발생한 문제와 해결
>
> 이 문서는 의사결정 흐름의 역사 기록 용도로만 보존된다.

---

## 1. 결정 사항 요약

| 항목 | 결정 |
|---|---|
| 1차 LLM | Gemini 2.5 Flash (무료 티어) |
| 폴백 LLM | GPT-5 mini |
| 출처/링크 | 모든 단계에서 매체명+링크 보존, LLM 출력에 인라인 표기 강제 |
| 본문 확보 | trafilatura → BeautifulSoup → User-Agent 변경 순 재시도 |
| 본문 실패 시 | 분야별 본문 확보 N건 미만일 때만 요약 기사로 보충 (하이브리드) |
| 분석 호출 | 단일 호출로 4분야 동시 분석 + 메가 트렌드 (호출 수 5→1) |
| 실패 알림 | Notion에 "오류 발생" 페이지 생성 (이메일/Slack 불요) |

## 2. 모듈 구조

```
news_reporter/
├── collectors.py       # RSS 수집 + 본문 크롤링 + dedup
├── llm.py              # Gemini/Claude 어댑터 (단일 인터페이스)
├── analyzer.py         # 단일 호출 분석 파이프라인
├── notion_writer.py    # 마크다운 → Notion 변환 + 업로드
├── main.py             # 오케스트레이션 + 에러 핸들링
├── requirements.txt
├── .github/workflows/daily.yml
└── README.md
```

## 3. 데이터 흐름

```
[RSS 피드들]
    ↓ feedparser
[Article 수집 (요약만)]
    ↓ trafilatura 본문 크롤링 (재시도 3단계)
[Article + body or summary_only flag]
    ↓ 유사 제목 dedup (Jaccard 0.7+)
[정제된 Article 리스트]
    ↓ 분야별 본문 충족도 체크 → 부족 시 요약 보충
[최종 분석 입력]
    ↓ Gemini 1회 호출 (실패 시 Claude Haiku)
[분석 결과 마크다운]
    ↓ Notion 변환
[Notion 페이지]
```

## 4. 핵심 인터페이스

### Article (확장)
```python
@dataclass
class Article:
    title: str
    summary: str          # RSS 요약
    body: str | None      # 본문 크롤링 성공 시
    link: str
    published: datetime
    category: str
    source: str           # 매체명 (편향 추적용)

    @property
    def has_body(self) -> bool:
        return self.body is not None and len(self.body) > 200

    @property
    def content_for_llm(self) -> str:
        # 본문 있으면 본문, 없으면 요약
        return self.body if self.has_body else self.summary
```

### LLM 추상화
```python
class LLMProvider(Protocol):
    def generate(self, system: str, user: str, max_tokens: int) -> str: ...

class GeminiProvider: ...
class OpenAIProvider: ...

def get_llm() -> LLMProvider:
    # GEMINI_API_KEY로 Gemini 시도, 실패 시 OPENAI_API_KEY로 GPT-5 mini 폴백
```

## 5. 본문 크롤링 재시도 전략

```
시도 1: trafilatura.fetch_url + extract (기본)
시도 2: requests(timeout=10) + trafilatura.extract(html)
시도 3: requests + UA="Mozilla/5.0..." + BeautifulSoup 본문 추출
실패: body=None 유지, summary만 보유
```

성공 판정: 본문 길이 >= 300자.

## 6. 하이브리드 보충 로직

```
분야별 목표: 본문 확보 기사 최소 5건
1. 본문 확보된 기사를 우선 정렬
2. 본문 확보 < 5건인 분야: 요약만 있는 기사로 8건까지 채움
3. LLM 입력 시 각 기사에 [본문] / [요약만] 태그
4. 프롬프트에 "[요약만] 태그 기사는 추측 자제" 명시
```

## 7. 유사 제목 dedup

같은 사건을 여러 매체가 다루는 경우 처리:
- 제목을 공백/조사 제거 후 토큰 집합화
- Jaccard 유사도 0.7 이상 → 중복으로 판단
- 본문 확보된 쪽을 보존, 매체 다양성을 위해 다른 매체 우선

## 8. 단일 호출 분석 구조

기존: 분야별 4회 + 종합 1회 = 5회 호출
신규: **모든 기사 한 번에 투입 → 1회 호출**

프롬프트:
```
[입력] 4개 분야의 어제 뉴스. 각 기사에 분야/매체/본문여부 태깅.
       예: [IT·테크·AI][전자신문][본문] 제목 / 본문 / 링크
[출력 구조]
  ## 🌐 오늘의 메가 트렌드 (먼저 작성: 분야 횡단 시각 확보)
  ## [분야명] × 4
    - 🎯 핵심 흐름 3줄
    - 📊 주요 이슈 심층 분석
    - 💡 전문가 인사이트
  ## 📎 참고 기사 (자동 생성, LLM 불필요)

[강제 규칙]
- 특정 사실 인용 시 매체명을 인라인 표기. 예: "전자신문에 따르면 ~"
- [요약만] 태그 기사는 추측 자제, 본문 기반 기사 위주 심층 분석
- 본문에 명시되지 않은 수치/인용은 만들지 말 것
```

토큰 추정:
- 입력: 60건 × 평균 1,500자(본문) ≈ 90,000자 ≈ 30,000 토큰
- 출력: 8,000 토큰
- Gemini 2.5 Flash 무료 한도 내. Claude Haiku 폴백 시 약 $0.07 (=100원)

## 9. Notion 변환 개선

기존: 마크다운 `**굵게**`, `[링크](url)` 무시.
신규: rich_text 파서 추가.

지원:
- `**굵게**` → `annotations.bold = true`
- `[텍스트](url)` → `text.link.url`
- 일반 텍스트

미지원 (의도적):
- 이탤릭, 인라인 코드 (분석 결과에 거의 안 나옴)
- 표 (분석 결과에 안 나옴)

## 10. 에러 핸들링 정책

| 단계 | 실패 시 동작 |
|---|---|
| RSS 0건 수집 | Notion에 "수집 실패" 페이지 생성 후 종료 |
| 본문 크롤링 실패 | summary로 폴백, body=None 유지 |
| Gemini 호출 실패 | GPT-5 mini로 재시도 |
| GPT-5 mini도 실패 | Notion에 "분석 실패" 페이지 + 수집 기사 목록만 게시 |
| Notion 업로드 실패 | 로컬 .md 파일로 저장 (CI 아티팩트로 보존) |

## 11. 환경변수

```
GEMINI_API_KEY        # 필수 (1차)
OPENAI_API_KEY        # 선택 (폴백). 없으면 Gemini 실패 = 알림만
NOTION_API_KEY        # 필수
NOTION_PARENT_PAGE_ID # 필수
```

## 12. 비용 예상

| 시나리오 | 월 비용 |
|---|---|
| Gemini만 사용 (무료 한도 내) | 0원 |
| Gemini 실패 → GPT-5 mini 폴백 (월 5회 가정) | ~200원 |
| Gemini 완전 장애 → 매일 GPT-5 mini | ~1,200원 |

GitHub Actions: private 저장소도 월 2,000분 무료. 회당 2~3분 예상.