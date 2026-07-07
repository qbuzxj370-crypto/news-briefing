"""스냅샷 분류 완전성 상태 테스트 (ADR-017, Phase 1a).

배경: 부분실패(미해결 기사 존재)를 스냅샷이 박제하던 문제의 토대. 스냅샷이
기사별 classification_unresolved를 round-trip하고 top-level complete를 기록해,
재실행 회복(2a)이 '어느 기사가 미해결인지'를 알 수 있게 한다. 구버전 스냅샷
(필드 없음)도 graceful하게 로드돼야 한다.

실행: python tests/test_snapshot_completeness.py
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import Article
from snapshot import save_snapshot, load_snapshot, snapshot_path

KST = timezone(timedelta(hours=9))


def _art(i, unresolved=False, category="정치"):
    return Article(
        title=f"제목{i}", summary="", link=f"https://x/{i}",
        published=datetime(2026, 6, 28, 8, tzinfo=KST), source="동아일보",
        category=(None if unresolved else category),
        classification_unresolved=unresolved,
    )


def test_unresolved_flag_round_trips():
    """기사별 미해결 플래그가 저장→복원에서 보존된다."""
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        save_snapshot("2026-06-28", [_art(1), _art(2, unresolved=True)], backup_dir=base)
        restored = load_snapshot("2026-06-28", backup_dir=base)
    assert restored is not None and len(restored) == 2
    assert restored[0].classification_unresolved is False
    assert restored[1].classification_unresolved is True
    assert restored[1].category is None


def test_complete_flag_true_when_all_resolved():
    """미해결이 하나도 없으면 payload.complete == True."""
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        p = save_snapshot("2026-06-28", [_art(1), _art(2)], backup_dir=base)
        payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["complete"] is True


def test_complete_flag_false_when_any_unresolved():
    """미해결이 하나라도 있으면 payload.complete == False."""
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        p = save_snapshot("2026-06-28", [_art(1), _art(2, unresolved=True)], backup_dir=base)
        payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["complete"] is False


def test_old_snapshot_without_flag_loads_gracefully():
    """구버전 스냅샷(classification_unresolved/complete 필드 없음)도 로드된다."""
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        base.mkdir(exist_ok=True)
        old = {
            "date_str": "2026-06-28",
            "created_at": "2026-06-28T07:00:00+09:00",
            # complete 없음
            "articles": [{
                "title": "옛 제목", "summary": "", "link": "https://x/1",
                "source": "동아일보", "body": None,
                "category": "정치", "is_cross_category": False,
                "published": "2026-06-28T08:00:00+09:00",
                # classification_unresolved 없음
            }],
        }
        snapshot_path("2026-06-28", base).write_text(
            json.dumps(old, ensure_ascii=False), encoding="utf-8"
        )
        restored = load_snapshot("2026-06-28", backup_dir=base)
    assert restored is not None and len(restored) == 1
    assert restored[0].classification_unresolved is False  # graceful 기본값
    assert restored[0].category == "정치"


if __name__ == "__main__":
    failures = 0
    tests = [
        test_unresolved_flag_round_trips,
        test_complete_flag_true_when_all_resolved,
        test_complete_flag_false_when_any_unresolved,
        test_old_snapshot_without_flag_loads_gracefully,
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
