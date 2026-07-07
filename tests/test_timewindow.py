"""수집 윈도우 고정 회귀 테스트.

배경 (2026-06-02 결정): 기존 윈도우는 실행 시각(now) 기준 48시간이라,
재실행/Actions 지연 시 윈도우가 평행이동해 표본이 바뀌었다 (06-01 브리핑이
08:27 실행 290건 vs 13:25 재실행 277건). cron 예정 시각(07:00 KST) 기준으로
고정해, 언제 실행하든 같은 윈도우·대상날짜가 나오도록 수정.

실행: python tests/test_timewindow.py
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timewindow import (
    collect_window,
    target_date_str,
    KST,
    SCHEDULED_HOUR_KST,
    COLLECT_WINDOW_HOURS,
)


def test_window_fixed_across_run_times():
    """같은 날 여러 실행 시각(정시/지연/수동)에서 윈도우·대상날짜 동일."""
    run_times = [
        datetime(2026, 6, 2, 7, 0, tzinfo=KST),   # 정상 cron
        datetime(2026, 6, 2, 8, 27, tzinfo=KST),  # 지연
        datetime(2026, 6, 2, 13, 25, tzinfo=KST), # 수동 재실행 (실제 케이스)
        datetime(2026, 6, 2, 23, 50, tzinfo=KST), # 늦은 밤
    ]
    windows = {collect_window(t) for t in run_times}
    dates = {target_date_str(t) for t in run_times}
    assert len(windows) == 1, f"윈도우가 실행 시각마다 다름: {windows}"
    assert len(dates) == 1, f"대상날짜가 실행 시각마다 다름: {dates}"


def test_window_values():
    """정상 cron(06-02 07:00) 실행 시 윈도우·대상날짜 값 검증."""
    now = datetime(2026, 6, 2, 7, 0, tzinfo=KST)
    start, end = collect_window(now)
    assert target_date_str(now) == "2026-06-01"
    assert end == datetime(2026, 6, 2, 7, 0, tzinfo=KST)      # 예정 시각
    assert start == datetime(2026, 5, 31, 7, 0, tzinfo=KST)   # 48h 전
    assert (end - start).total_seconds() / 3600 == COLLECT_WINDOW_HOURS


def test_before_scheduled_uses_previous():
    """예정 시각(07:00) 이전 실행이면 직전 예정 시각 기준 (수동 새벽 실행 방어)."""
    now = datetime(2026, 6, 2, 5, 30, tzinfo=KST)  # 07:00 전
    start, end = collect_window(now)
    # 06-02 07:00이 아직 안 됐으므로 직전 예정 = 06-01 07:00
    assert end == datetime(2026, 6, 1, 7, 0, tzinfo=KST)
    assert target_date_str(now) == "2026-05-31"


def test_date_and_window_always_consistent():
    """대상날짜 = 윈도우 끝의 전날. 항상 정합 (자정 근처 불일치 방지)."""
    # 자정 직후, 정각 직전, 정각, 오후 — 다양한 시각
    for hour, minute in [(0, 30), (6, 59), (7, 0), (15, 0), (23, 59)]:
        now = datetime(2026, 6, 2, hour, minute, tzinfo=KST)
        _, end = collect_window(now)
        d = target_date_str(now)
        # 대상날짜는 윈도우 끝(예정 시각)의 전날이어야 함
        expected = (end.date()).isoformat()
        # end가 D+1이므로 대상날짜 D = end - 1일
        from datetime import timedelta
        assert d == (end - timedelta(days=1)).strftime("%Y-%m-%d"), (
            f"{hour}:{minute} — 대상날짜 {d} ≠ 윈도우끝 전날 (end={end.isoformat()})"
        )


def test_scheduled_hour_is_7():
    """상수가 cron(07:00 KST)과 일치하는지. 변경 시 cron도 같이 바꿀 것."""
    assert SCHEDULED_HOUR_KST == 7, (
        "SCHEDULED_HOUR_KST 변경 시 .github/workflows cron도 동기화 필요"
    )


if __name__ == "__main__":
    failures = 0
    tests = [
        test_window_fixed_across_run_times,
        test_window_values,
        test_before_scheduled_uses_previous,
        test_date_and_window_always_consistent,
        test_scheduled_hour_is_7,
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