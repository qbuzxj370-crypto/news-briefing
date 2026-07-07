"""Slack 브리핑 미해결 경고 렌더링 테스트 (ADR-017, Phase 1b).

배경: 부분 분류 실패로 일부 분야가 누락된 degraded 브리핑임을 Slack에서
운영자가 인지할 수 있어야 한다("재실행하면 회복"). 미해결 0건이면 경고 없음.

실행: python tests/test_slack_unresolved.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slack_writer import _briefing_blocks

_ANALYSIS = {
    "mega_trend": {"key_threads": ["흐름1"]},
    "categories": [{"name": "정치", "deep_issues": [{"title": "이슈A"}]}],
}


def _all_text(blocks):
    out = []
    for b in blocks:
        t = b.get("text")
        if isinstance(t, dict):
            out.append(t.get("text", ""))
        for el in b.get("elements", []):
            if isinstance(el, dict):
                tt = el.get("text")
                if isinstance(tt, dict):
                    out.append(tt.get("text", ""))
                elif isinstance(tt, str):
                    out.append(tt)
    return "\n".join(out)


def _blocks(stats):
    return _briefing_blocks("2026-06-28", "오늘의 핵심", _ANALYSIS, "pageid123", {}, stats)


def test_unresolved_warning_shown():
    """미해결 N건이면 경고 문구와 건수가 표시된다."""
    text = _all_text(_blocks({"article_count": 40, "unresolved": 12}))
    assert "미해결" in text, text
    assert "12" in text, text


def test_no_warning_when_resolved():
    """미해결 0건이면 경고 없음."""
    text = _all_text(_blocks({"article_count": 40, "unresolved": 0}))
    assert "미해결" not in text, text


def test_no_warning_when_stats_absent():
    """stats가 없어도 안전(경고 없음, 예외 없음)."""
    text = _all_text(_blocks(None))
    assert "미해결" not in text, text


if __name__ == "__main__":
    failures = 0
    tests = [
        test_unresolved_warning_shown,
        test_no_warning_when_resolved,
        test_no_warning_when_stats_absent,
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
