"""robots.txt 판정 프로브 — 피드별 실제 기사 링크의 크롤링 허용 여부 점검 (ADR-018).

운영 중 특정 매체의 본문 확보율이 급락하면(TROUBLESHOOTING R5) 실행해
매체의 robots 정책 변경인지, robots.txt 로드 오류(5xx 등)인지 구분한다.

- `N/N 허용`: 정상 — 본문 급락은 다른 원인 (크롤링 차단, 페이지 구조 변경 등).
- `0/N 허용` + `[robots] <host>: HTTP 5xx/로드 실패` 로그: 일시 오류 가능성.
- `0/N 허용` + 로그 없음: 매체가 robots 규칙을 바꿈 — 준수 대상. 해당 매체
  본문은 포기(요약 폴백)하거나 대체 매체 검토.

실행: python robots_probe.py
"""

import feedparser

from collectors import RSS_FEEDS, _robots_allowed

if __name__ == "__main__":
    for feed in RSS_FEEDS:
        parsed = feedparser.parse(feed)
        links = [e.get("link", "") for e in parsed.entries[:5] if e.get("link")]
        allowed = sum(1 for link in links if _robots_allowed(link))
        if not links:
            print(f"[확인 필요] 엔트리 0건 (피드 자체 점검 — R1) — {feed}")
        elif allowed == len(links):
            print(f"[OK] {allowed}/{len(links)} 허용 — {feed}")
        else:
            print(f"[확인 필요] {allowed}/{len(links)} 허용 — {feed}")
