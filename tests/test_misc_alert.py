"""MISC("기타") 분류 비율 임계 경보 테스트.

배경 (ADR-012/013 미해결 이슈): "기타"로 분류된 기사는 분석에서 제외되는데
(silent drop), 분류 품질이 무너지면(응답 잘림·프롬프트 회귀 등) 기타 비율이
급증해도 아무 신호 없이 분석 표본만 조용히 줄어든다. ADR이 "저비용이라 우선
구현 후보"로 지목한 임계 경보를 classifier.check_misc_ratio로 구현.

실행: python tests/test_misc_alert.py
"""

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classifier import check_misc_ratio, FALLBACK_CATEGORY, MISC_RATIO_WARN_THRESHOLD


def _run(stats, total, **kwargs):
    """check_misc_ratio 실행, (반환 비율, 출력 텍스트) 반환."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        ratio = check_misc_ratio(stats, total, **kwargs)
    return ratio, buf.getvalue()


def test_normal_ratio_no_warning():
    """평시 수준(기타 ~15%)에서는 경고가 없어야 한다."""
    stats = {FALLBACK_CATEGORY: 15, "정치": 40, "IT·테크·AI": 45}
    ratio, out = _run(stats, 100)
    assert abs(ratio - 0.15) < 1e-9, ratio
    assert "[경고]" not in out, f"평시 비율인데 경고 출력: {out!r}"


def test_high_ratio_warns():
    """임계(30%) 초과 시 경고가 출력되어야 한다."""
    stats = {FALLBACK_CATEGORY: 40, "정치": 60}
    ratio, out = _run(stats, 100)
    assert abs(ratio - 0.40) < 1e-9, ratio
    assert "[경고]" in out, "임계 초과인데 경고 없음"
    assert "기타" in out and "40%" in out, f"경고 내용 부족: {out!r}"


def test_all_misc_warns():
    """전량 기타(분류 전면 실패 폴백)도 경고되어야 한다."""
    stats = {FALLBACK_CATEGORY: 50}
    ratio, out = _run(stats, 50)
    assert ratio == 1.0
    assert "[경고]" in out


def test_boundary_not_warned():
    """정확히 임계값(30%)은 '초과'가 아니므로 경고하지 않는다."""
    stats = {FALLBACK_CATEGORY: 30, "정치": 70}
    ratio, out = _run(stats, 100)
    assert abs(ratio - MISC_RATIO_WARN_THRESHOLD) < 1e-9
    assert "[경고]" not in out, f"임계 동률은 경고 대상 아님: {out!r}"


def test_zero_total_safe():
    """기사 0건이어도 division error 없이 0.0 반환."""
    ratio, out = _run({}, 0)
    assert ratio == 0.0
    assert "[경고]" not in out


if __name__ == "__main__":
    failures = 0
    tests = [
        test_normal_ratio_no_warning,
        test_high_ratio_warns,
        test_all_misc_warns,
        test_boundary_not_warned,
        test_zero_total_safe,
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
