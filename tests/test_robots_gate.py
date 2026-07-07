"""robots.txt 준수 게이트 회귀 테스트 (ADR-018).

배경 (2026-07-07): 본문 크롤링이 robots.txt를 확인하지 않고 기사 페이지를
요청했다. 공개 저장소 전환 점검에서 법적 노출 지점으로 확인되어, 페이지
요청(fetch_body·_date_from_page) 전에 호스트별 robots.txt 판정을 넣었다.

핵심 규칙 (collectors._load_robots / _robots_allowed):
- 200: 파싱해 URL별 판정. 우리 UA(브라우저 계열)는 명명된 AI봇 그룹
  (ClaudeBot 등)에 매칭되지 않고 `*` 그룹을 따른다.
- 4xx: robots 부재로 간주 → 허용.
- 5xx·네트워크 오류: 판단 불가 → 보수적으로 차단 (요약 폴백이 흡수).
- 호스트별 캐시: robots.txt는 호스트당 1회만 요청.

함정 회귀: RobotFileParser.read()는 Python-urllib UA로 요청해 403을 받으면
전체 차단으로 오판한다(한겨레·매경에서 실측). 반드시 requests+UA_DESKTOP으로
받아 parse()해야 한다 — 이 테스트는 그 우회 구현을 전제로 한다.

실행: python tests/test_robots_gate.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import collectors
from collectors import _robots_allowed, fetch_body


class FakeResp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def _with_robots_response(fn_or_exc):
    """collectors.requests.get을 대체하고 (원본, 호출 URL 기록) 반환."""
    calls = []
    original = collectors.requests.get

    def fake_get(url, **kwargs):
        calls.append(url)
        if isinstance(fn_or_exc, Exception):
            raise fn_or_exc
        return fn_or_exc

    collectors.requests.get = fake_get
    return original, calls


def _reset():
    collectors._robots_cache.clear()


def test_disallow_blocks_without_page_fetch():
    """차단 규칙에 걸리면 기사 페이지 요청 자체가 없다."""
    _reset()
    robots = "User-agent: *\nDisallow: /news/\n"
    original, _ = _with_robots_response(FakeResp(200, robots))
    fetch_calls = []
    orig_fetchers = (collectors._fetch_with_trafilatura,
                     collectors._fetch_with_requests_trafilatura,
                     collectors._fetch_with_bs4)
    collectors._fetch_with_trafilatura = lambda url: fetch_calls.append(url) or "x"
    try:
        body = fetch_body("https://example.com/news/123")
    finally:
        collectors.requests.get = original
        (collectors._fetch_with_trafilatura,
         collectors._fetch_with_requests_trafilatura,
         collectors._fetch_with_bs4) = orig_fetchers
    assert body is None
    assert fetch_calls == [], fetch_calls


def test_wildcard_group_applies_not_named_bots():
    """AI봇 그룹(ClaudeBot 등)의 차단은 우리 UA에 적용되지 않는다 — `*` 그룹을 따른다.

    매경·경향·한겨레의 실제 robots 패턴 회귀 (AI봇 전면 차단 + `*` 허용).
    """
    _reset()
    robots = (
        "User-agent: ClaudeBot\nUser-agent: GPTBot\nDisallow: /\n\n"
        "User-agent: *\nDisallow: /admin/\n"
    )
    original, _ = _with_robots_response(FakeResp(200, robots))
    try:
        allowed_article = _robots_allowed("https://example.com/news/123")
        blocked_admin = _robots_allowed("https://example.com/admin/x")
    finally:
        collectors.requests.get = original
    assert allowed_article is True
    assert blocked_admin is False


def test_4xx_treated_as_allow():
    """robots.txt 404 → 제한 없음으로 간주 (RFC 9309)."""
    _reset()
    original, _ = _with_robots_response(FakeResp(404))
    try:
        assert _robots_allowed("https://example.com/a") is True
    finally:
        collectors.requests.get = original


def test_5xx_treated_as_deny():
    """robots.txt 5xx → 판단 불가, 보수적으로 차단."""
    _reset()
    original, _ = _with_robots_response(FakeResp(503))
    try:
        assert _robots_allowed("https://example.com/a") is False
    finally:
        collectors.requests.get = original


def test_network_error_treated_as_deny():
    """robots.txt 로드 예외 → 보수적으로 차단."""
    _reset()
    original, _ = _with_robots_response(ConnectionError("boom"))
    try:
        assert _robots_allowed("https://example.com/a") is False
    finally:
        collectors.requests.get = original


def test_cache_one_request_per_host():
    """같은 호스트는 robots.txt를 1회만 요청한다."""
    _reset()
    original, calls = _with_robots_response(FakeResp(200, "User-agent: *\nAllow: /\n"))
    try:
        _robots_allowed("https://example.com/a")
        _robots_allowed("https://example.com/b")
        _robots_allowed("https://example.com/c")
    finally:
        collectors.requests.get = original
    assert len(calls) == 1, calls


def test_date_from_page_respects_robots():
    """_date_from_page도 차단 시 페이지를 받지 않고 (None, None)."""
    _reset()
    original, _ = _with_robots_response(FakeResp(200, "User-agent: *\nDisallow: /\n"))
    fetch_calls = []
    orig_fetch = collectors.trafilatura.fetch_url
    collectors.trafilatura.fetch_url = lambda url, no_ssl=True: fetch_calls.append(url) or None
    try:
        dt, body = collectors._date_from_page("https://example.com/arti/1.html")
    finally:
        collectors.requests.get = original
        collectors.trafilatura.fetch_url = orig_fetch
    assert dt is None and body is None
    assert fetch_calls == [], fetch_calls


if __name__ == "__main__":
    failures = 0
    tests = [
        test_disallow_blocks_without_page_fetch,
        test_wildcard_group_applies_not_named_bots,
        test_4xx_treated_as_allow,
        test_5xx_treated_as_deny,
        test_network_error_treated_as_deny,
        test_cache_one_request_per_host,
        test_date_from_page_respects_robots,
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
