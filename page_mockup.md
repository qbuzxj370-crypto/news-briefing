# 페이지 구조 최종 모형

## 1. Notion DB 속성

| 속성 | 타입 | 예시 | 용도 |
|---|---|---|---|
| 제목 | Title | `2026-05-22 데일리 브리핑` | 페이지 식별 |
| 날짜 | Date | `2026-05-22` | 캘린더뷰, 정렬 |
| 모델 | Select | `gemini-2.5-flash` / `gpt-5-mini` | 폴백 감지 |
| 본문 확보율 | Number (%) | `73` | 분석 품질 지표 |
| 분야 태그 | Multi-select | `IT·테크·AI`, `정치`, `사회·시사` | 데이터 있는 분야 |
| 분석 기사 수 | Number | `34` | 분량 지표 |
| 생성 시각 | Created time | (자동) | Notion 자동 기록 |

---

## 2. 페이지 본문 (최종)

```markdown
# 📰 2026-05-22 데일리 브리핑

> 💡 **오늘의 핵심**
> 미·중 반도체 갈등이 다시 격화되는 가운데 국내 기업은 HBM 양산 경쟁에
> 박차. 정부의 부동산 규제 완화 시그널이 시장에 영향.

---

## 🌐 오늘의 메가 트렌드

여러 분야 횡단 흐름 8~12줄...

**분야 횡단 흐름**
- 미·중 기술 패권 경쟁이 반도체에서 AI 모델 규제로 확대
- 정부의 부동산 시그널, 증시·산업 정책에 연쇄 영향

---

## 💻 IT·테크·AI

### 🎯 핵심 흐름
- 삼성전자, HBM4 양산 본격화 발표
- 정부, AI 기본법 시행령 초안 공개
- 네이버·카카오, 클라우드 사업 구조조정

▶ ### 📊 삼성전자 HBM4 양산 본격화           ← 토글 헤더 (펼치면 아래)
   - **맥락**: 엔비디아 차세대 칩 일정에 맞춘 공급 확보 경쟁.
   - **함의**: HBM 시장 점유율 재편 가능성. 메모리 사이클 회복 신호.
   - **관전 포인트**: 수율 안정화 시점, 엔비디아 공식 채택 발표.
   
   📰 관련 기사
   - [전자신문] [삼성전자, HBM4 12단 양산 본격화...](URL1)
   - [한국경제] [삼성 HBM 시장 재편 노린다...](URL2)

▶ ### 📊 네이버 클라우드 구조조정             ← 토글 (접힘)
▶ ### 📊 AI 기본법 시행령 초안                ← 토글 (접힘)

▶ ### 📌 그 외 이슈 (4건)                     ← 토글 (접힘, 펼치면 아래)
   - **카카오 데이터센터 화재 후속** ([한국경제](URL))
     11일 만에 모든 서비스 정상화. 이중화 체계 작동 입증.
   - **LG CNS 1월 상장 예정** ([전자신문](URL))
     IPO 신청서 제출. 클라우드·AI 자금 조달 본격화.
   - **(이슈 3)** ([매체](URL))
     ...
   - **(이슈 4)** ([매체](URL))
     ...

### 💡 전문가 인사이트
표면 뉴스에 가려진 구조적 변화 1~2가지...

---

## 💰 경제·금융·증시
(동일 구조)

## 🏛️ 정치
(동일 구조)

## 🗞️ 사회·시사
(동일 구조)

## 🏭 산업
(동일 구조)

---

_수집 매체: 전자신문 · 블로터 · ZDNet Korea · 한국경제 · 매일경제 · 동아일보 · 연합뉴스 · 한겨레_
```

---

## 3. 토글 마커 규약 (Jinja2 출력 → Notion 파서)

마크다운에는 토글 헤더 표준이 없으므로 **`▶ ### ...`** prefix를 마커로 사용.
- `▶ ### 텍스트` → Notion `heading_3` + `is_toggleable=True`
- 마커 뒤 인덴트된 블록들이 토글 children

로컬 백업 .md에서는 `▶` 그대로 노출되어도 의미 통함.

---

## 4. JSON 스키마 (LLM 출력)

```json
{
  "tldr": "2~3줄 핵심 한 문단",
  "mega_trend": {
    "summary": "8~12줄 본문",
    "key_threads": ["분야 횡단 흐름 1", "흐름 2"]
  },
  "categories": [
    {
      "name": "IT·테크·AI",
      "has_sufficient_data": true,
      "limitation_note": null,
      "key_flows": ["한 줄 1", "한 줄 2", "한 줄 3"],
      "deep_issues": [
        {
          "title": "삼성전자 HBM4 양산 본격화",
          "context": "맥락...",
          "implication": "함의...",
          "watch_points": "관전 포인트...",
          "referenced_article_ids": [3, 7]
        }
      ],
      "minor_issues": [
        {
          "title": "카카오 데이터센터 화재 후속",
          "summary": "11일 만에 모든 서비스 정상화.",
          "implication": "이중화 체계 작동 입증.",
          "referenced_article_ids": [12]
        }
      ],
      "insight": "전문가 인사이트..."
    }
  ]
}
```

### 필드 규칙
- `key_flows`: 1~3개 유연 (분야 데이터 부족 시 적게)
- `deep_issues`: 2~4개 유연, 데이터 부족 시 0~1개
- `minor_issues`: 상한 없음. 가이드라인 - 광고성/인사/단순 동향 제외, 의미 있는 이슈만
- `referenced_article_ids`: 각 이슈가 참조한 입력 기사 번호 배열 (정수)
- `has_sufficient_data`: false면 limitation_note에 한 줄 설명

---

## 5. 분야별 이모지 (코드 매핑)

```python
CATEGORY_EMOJI = {
    "IT·테크·AI": "💻",
    "경제·금융·증시": "💰",
    "정치": "🏛️",
    "사회·시사": "🗞️",
    "산업": "🏭",
}
```

---

## 6. RSS 5분야 구성 (실제 운영)

`rss_probe.py`로 작동 검증 후 확정된 RSS 목록. 죽은 매체(블로터, ZDNet, 한국경제 일부 카테고리, 매일경제 일부 카테고리) 교체 + 정치/사회 매체 다양화 반영.

```python
RSS_FEEDS = {
    "IT·테크·AI": [
        "https://it.chosun.com/rss/allArticle.xml",       # IT조선
        "https://rss.etnews.com/Section902.xml",          # 전자신문 IT
        "https://rss.etnews.com/Section901.xml",          # 전자신문 (다른 섹션)
    ],
    "경제·금융·증시": [
        "https://rss.mt.co.kr/mt_news.xml",               # 머니투데이
        "https://www.hankyung.com/feed/economy",          # 한국경제
        "https://www.mk.co.kr/rss/30000001/",             # 매일경제
    ],
    "정치": [
        "https://rss.donga.com/politics.xml",             # 동아일보 (보수)
        "https://www.khan.co.kr/rss/rssdata/politic_news.xml",  # 경향신문 (진보)
        "https://rss.ohmynews.com/rss/politics.xml",      # 오마이뉴스 (진보)
    ],
    "사회·시사": [
        "https://www.yna.co.kr/rss/society.xml",          # 연합뉴스 (중립)
        "https://rss.donga.com/national.xml",             # 동아 사회 (보수)
        "https://www.hani.co.kr/rss/society/",            # 한겨레 (진보)
    ],
    "산업": [
        "https://www.mk.co.kr/rss/50100032/",             # 매일경제 산업
        "https://rss.etnews.com/Section903.xml",          # 전자신문 산업
    ],
}
```

RSS URL 검증 도구: `python rss_probe.py` 실행 시 작동 매체 + 추천 RSS_FEEDS 출력. 매체 추가/변경 시 활용.