"""선별 후보 본문 크롤링(enrich_with_bodies target 모드) 회귀 테스트 (ADR-018).

배경 (2026-07-07): 구버전은 수집 직후 전량(윈도우 내 수백 건)의 기사 페이지를
크롤링했다. 분석에 실제로 쓰이는 것은 분야별 8~20건뿐이므로, 본문 크롤링을
분류·dedup 후 분야별 후보로 미루고 목표 도달 시 중단하도록 변경 — 매체 서버
요청 수와 저작물 복제 범위를 최소화한다 (main.py step 3).

핵심 규칙:
- target 지정 시 최신 기사부터 시도, 본문 확보가 target에 닿으면 중단.
- 이미 본문 있는 기사(한겨레 prefetch)는 요청 없이 target에 계상.
- 실패가 이어져도 시도 max_attempts(기본 target*3)에서 중단 — 요청 폭주 방지.
- target=None이면 전량 시도 (구버전 호환).

실행: python tests/test_enrich_candidates.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import collectors
from collectors import Article, enrich_with_bodies

KST = timezone(timedelta(hours=9))
BASE = datetime(2026, 7, 6, 9, 0, tzinfo=KST)
LONG_BODY = "본문 문단입니다. " * 60  # has_body 기준(300자) 충족


def _articles(n: int, body_indices=()) -> list:
    """i가 클수록 최신인 기사 n건. body_indices의 기사는 본문 확보 상태."""
    return [
        Article(
            title=f"기사 {i}", summary=f"요약 {i}", link=f"https://x.com/{i}",
            published=BASE + timedelta(hours=i), source="테스트",
            body=LONG_BODY if i in body_indices else None,
        )
        for i in range(n)
    ]


def _patch_fetch(result_fn):
    """collectors.fetch_body를 대체. (원본, 호출 URL 기록) 반환."""
    calls = []
    original = collectors.fetch_body

    def fake(url):
        calls.append(url)
        return result_fn(url)

    collectors.fetch_body = fake
    return original, calls


def test_stops_at_target_newest_first():
    """target 도달 시 중단하며 최신 기사부터 시도한다."""
    arts = _articles(10)
    original, calls = _patch_fetch(lambda url: LONG_BODY)
    try:
        enrich_with_bodies(arts, sleep_sec=0, target=3)
    finally:
        collectors.fetch_body = original
    # 최신 3건(i=9,8,7)만 요청
    assert calls == ["https://x.com/9", "https://x.com/8", "https://x.com/7"], calls
    assert sum(1 for a in arts if a.has_body) == 3


def test_prefetched_counts_toward_target():
    """이미 본문 있는 기사는 요청 없이 목표에 계상된다."""
    arts = _articles(5, body_indices={4, 3})  # 최신 2건이 이미 본문 확보
    original, calls = _patch_fetch(lambda url: LONG_BODY)
    try:
        enrich_with_bodies(arts, sleep_sec=0, target=3)
    finally:
        collectors.fetch_body = original
    assert calls == ["https://x.com/2"], calls


def test_attempt_cap_on_persistent_failure():
    """크롤링이 계속 실패해도 시도는 target*3에서 멈춘다."""
    arts = _articles(20)
    original, calls = _patch_fetch(lambda url: None)
    try:
        enrich_with_bodies(arts, sleep_sec=0, target=2)
    finally:
        collectors.fetch_body = original
    assert len(calls) == 6, len(calls)  # 2*3


def test_no_target_fetches_all():
    """target=None이면 전량 시도 (구버전 호환)."""
    arts = _articles(7)
    original, calls = _patch_fetch(lambda url: None)
    try:
        enrich_with_bodies(arts, sleep_sec=0)
    finally:
        collectors.fetch_body = original
    assert len(calls) == 7, len(calls)


if __name__ == "__main__":
    failures = 0
    tests = [
        test_stops_at_target_newest_first,
        test_prefetched_counts_toward_target,
        test_attempt_cap_on_persistent_failure,
        test_no_target_fetches_all,
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
