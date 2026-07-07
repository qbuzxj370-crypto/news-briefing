"""RSS 무날짜 엔트리의 페이지 날짜 추출 폴백 회귀 테스트.

배경 (2026-06-10 진단): 한겨레 RSS는 엔트리에 날짜 필드가 아예 없어
(published/updated/dc:date 전무) _parse_published가 전량 None → 수집 0건으로
며칠간 조용히 빠졌다(06-04~09 산출물 부재). 피드 자체는 정상(30건)이므로
기사 페이지 메타데이터에서 날짜를 추출하는 _date_from_page 폴백을 추가.

원칙: 날짜는 'YYYY-MM-DD'에 정오(12:00 KST) 고정 부여 — 같은 입력이면 언제
실행해도 같은 윈도우 판정 (ADR-015 재실행 결정론 유지). 추출 실패 시 스킵.

실행: python tests/test_date_fallback.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import collectors
from collectors import _date_from_page, enrich_with_bodies, Article

# 이 파일은 robots 게이트 이후의 로직을 검증한다 — 게이트를 항상 허용으로
# 대체해 robots.txt 네트워크 요청을 차단. 게이트 자체는 test_robots_gate.py.
collectors._robots_allowed = lambda url: True

KST = timezone(timedelta(hours=9))

# article:published_time 메타를 가진 합성 기사 페이지 (한겨레 페이지 구조 모사)
HTML_WITH_DATE = """<html><head>
<meta property="og:title" content="테스트 기사"/>
<meta property="article:published_time" content="2026-06-10T14:30:00+09:00"/>
</head><body><article><p>본문 문단입니다. 날짜 추출과 본문 재사용을 검증합니다.</p>
<p>두 번째 문단으로 본문 추출이 동작하는지 확인합니다.</p></article></body></html>"""

HTML_NO_DATE = "<html><head></head><body><p>날짜 메타 없음</p></body></html>"


def _patch_fetch(return_value):
    """collectors.trafilatura.fetch_url을 임시 대체. 원본 반환."""
    original = collectors.trafilatura.fetch_url
    collectors.trafilatura.fetch_url = lambda url, no_ssl=True: return_value
    return original


def test_date_extracted_noon_kst():
    """메타 날짜가 정오 KST datetime으로 변환되고 본문도 함께 확보된다."""
    original = _patch_fetch(HTML_WITH_DATE)
    try:
        dt, body = _date_from_page("https://www.hani.co.kr/arti/society/x.html")
    finally:
        collectors.trafilatura.fetch_url = original
    assert dt == datetime(2026, 6, 10, 12, 0, tzinfo=KST), dt
    assert body and "본문 문단" in body, body


def test_deterministic_across_calls():
    """같은 페이지면 호출 시점과 무관하게 같은 시각 — 재실행 결정론(ADR-015)."""
    original = _patch_fetch(HTML_WITH_DATE)
    try:
        dt1, _ = _date_from_page("https://x.com/a")
        dt2, _ = _date_from_page("https://x.com/a")
    finally:
        collectors.trafilatura.fetch_url = original
    assert dt1 == dt2


def test_no_date_meta_returns_none():
    """날짜 메타가 없으면 (None, None) — 추정하지 않고 스킵."""
    original = _patch_fetch(HTML_NO_DATE)
    try:
        dt, body = _date_from_page("https://x.com/b")
    finally:
        collectors.trafilatura.fetch_url = original
    assert dt is None and body is None


def test_fetch_failure_returns_none():
    """페이지 다운로드 실패 시 (None, None)."""
    original = _patch_fetch(None)
    try:
        dt, body = _date_from_page("https://x.com/c")
    finally:
        collectors.trafilatura.fetch_url = original
    assert dt is None and body is None


def test_enrich_skips_prefetched_body():
    """수집 단계에서 본문을 이미 확보한 기사는 재크롤링하지 않는다."""
    calls = []
    original = collectors.fetch_body
    collectors.fetch_body = lambda url: calls.append(url) or "크롤링 본문" * 100
    try:
        prefetched = Article(
            title="이미 본문 있음", summary="", link="https://x.com/1",
            published=datetime.now(KST), source="한겨레",
            body="확보된 본문 " * 60,  # has_body 기준(300자) 충족
        )
        short_body = Article(
            title="본문 짧음", summary="", link="https://x.com/2",
            published=datetime.now(KST), source="한겨레",
            body="짧음",  # 300자 미만 → 3단계 크롤링 대상
        )
        enrich_with_bodies([prefetched, short_body], sleep_sec=0)
    finally:
        collectors.fetch_body = original
    assert calls == ["https://x.com/2"], calls
    assert prefetched.body.startswith("확보된 본문")


if __name__ == "__main__":
    failures = 0
    tests = [
        test_date_extracted_noon_kst,
        test_deterministic_across_calls,
        test_no_date_meta_returns_none,
        test_fetch_failure_returns_none,
        test_enrich_skips_prefetched_body,
    ]
    for fn in tests:
        try:
            fn()
            print(f"  PASS: {fn.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL: {fn.__name__}{e}")
    if failures:
        print(f"\n{failures}개 실패")
        sys.exit(1)
    print("\n전체 통과")
