"""수집·분류 스냅샷 저장/복원 회귀 테스트.

배경 (2026-06-11 운영 관찰): 1차 실행이 분석 단계에서 실패(Gemini 503)한 뒤
오후 재실행 시 RSS 피드 top-N이 갱신되어 표본이 314건→127건으로 줄고 5개
매체가 통째 누락됐다. snapshot.py가 수집+분류 완료 표본을 저장하고, 같은
대상 날짜의 재실행은 그 표본으로 분석부터 재개한다.

원칙: 스냅샷은 최적화일 뿐 — 없거나 손상이거나 날짜가 다르면 None을 반환해
신규 수집으로 자연스럽게 넘어가야 한다 (파이프라인을 멈추면 안 됨).

본문 제외 (ADR-019, 2026-07-07): 공개 저장소의 Actions 아티팩트는 누구나
받을 수 있어 기사 전문을 담으면 저작물 공중 전송이 된다. 스냅샷은 본문을
저장하지 않으며(파일에 body 부재), 재실행은 선별 후보만 재크롤링한다.
구버전(body 포함) 스냅샷도 로드는 되고 body만 무시된다.

실행: python tests/test_snapshot.py
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


def _articles():
    return [
        Article(
            title="제목 하나", summary="요약", link="https://x.com/1",
            published=datetime(2026, 6, 10, 8, 30, tzinfo=KST),
            source="한겨레", body="본문 " * 100,
            category="사회·시사", is_cross_category=True,
        ),
        Article(
            title="제목 둘", summary="", link="https://x.com/2",
            published=datetime(2026, 6, 10, 12, 0, tzinfo=KST),
            source="IT조선", body=None,
            category="IT·테크·AI", is_cross_category=False,
        ),
    ]


def test_round_trip_preserves_fields():
    """저장 → 복원 후 표본 필드(분류·tz 포함 시각)가 보존된다. 본문은 제외."""
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        originals = _articles()
        save_snapshot("2026-06-10", originals, backup_dir=base)
        restored = load_snapshot("2026-06-10", backup_dir=base)
    assert restored is not None and len(restored) == 2
    for a, b in zip(originals, restored):
        assert a.title == b.title
        assert a.published == b.published, (a.published, b.published)
        assert a.published.tzinfo is not None
        assert b.body is None  # 본문은 저장하지 않음 (ADR-019)
        assert a.category == b.category
        assert a.is_cross_category == b.is_cross_category
        assert a.source == b.source


def test_saved_file_contains_no_body():
    """저장된 JSON에 body 키도, 본문 내용도 없다 — 공개 아티팩트 안전 (ADR-019)."""
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        p = save_snapshot("2026-06-10", _articles(), backup_dir=base)
        payload = json.loads(p.read_text(encoding="utf-8"))
    raw = p.name + json.dumps(payload, ensure_ascii=False)
    assert all("body" not in art for art in payload["articles"]), payload["articles"][0].keys()
    assert "본문 본문" not in raw  # _articles()의 body 내용이 어디에도 없음


def test_legacy_snapshot_with_body_loads():
    """구버전 스냅샷(body 필드 포함)도 로드되며 body는 무시된다."""
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        # 구버전 포맷 재현: body 포함
        payload = {
            "date_str": "2026-06-10",
            "complete": True,
            "articles": [{
                "title": "구버전", "summary": "요약", "link": "https://x.com/1",
                "source": "한겨레", "body": "본문 " * 100,
                "category": "사회·시사", "is_cross_category": False,
                "published": datetime(2026, 6, 10, 8, 0, tzinfo=KST).isoformat(),
            }],
        }
        base.mkdir(exist_ok=True)
        snapshot_path("2026-06-10", base).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        restored = load_snapshot("2026-06-10", backup_dir=base)
    assert restored is not None and len(restored) == 1
    assert restored[0].title == "구버전"
    assert restored[0].body is None


def test_missing_returns_none():
    """스냅샷이 없으면 None — 신규 수집 경로로 진행."""
    with tempfile.TemporaryDirectory() as d:
        assert load_snapshot("2026-06-10", backup_dir=Path(d)) is None


def test_other_date_ignored():
    """다른 날짜의 스냅샷은 무시된다 (Actions가 직전 실행 것을 받아와도 무해)."""
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        save_snapshot("2026-06-09", _articles(), backup_dir=base)
        assert load_snapshot("2026-06-10", backup_dir=base) is None


def test_corrupt_file_returns_none():
    """손상된 스냅샷은 None — 예외로 파이프라인을 멈추지 않는다."""
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        snapshot_path("2026-06-10", base).parent.mkdir(exist_ok=True)
        snapshot_path("2026-06-10", base).write_text("{잘림", encoding="utf-8")
        assert load_snapshot("2026-06-10", backup_dir=base) is None


def test_date_key_inside_payload_checked():
    """파일명을 바꿔치기해도 내용의 date_str이 다르면 무시한다."""
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        p = save_snapshot("2026-06-09", _articles(), backup_dir=base)
        p.rename(snapshot_path("2026-06-10", base))
        assert load_snapshot("2026-06-10", backup_dir=base) is None


if __name__ == "__main__":
    failures = 0
    tests = [
        test_round_trip_preserves_fields,
        test_saved_file_contains_no_body,
        test_legacy_snapshot_with_body_loads,
        test_missing_returns_none,
        test_other_date_ignored,
        test_corrupt_file_returns_none,
        test_date_key_inside_payload_checked,
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
