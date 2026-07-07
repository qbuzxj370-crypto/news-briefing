"""날짜 간 중복 기사 측정 도구(dup_probe) 단위 테스트.

배경 (ADR-015): 48h 윈도우로 어제 분석한 기사가 오늘 보고서에 재등장할 수
있다. 처리 방침은 설계 확정·측정 대기 — dup_probe.py가 백업 md에서 날짜별
URL을 추출해 재등장 빈도를 측정한다. 이 테스트는 추출·집계 로직을 픽스처로
검증한다 (실측은 운영 backups/ 데이터로).

실행: python tests/test_dup_probe.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dup_probe import extract_urls, load_backup_urls, cross_date_overlaps


SAMPLE_MD = """# 📰 2026-06-08 데일리 브리핑
## 💻 IT·테크·AI
\t📰 관련 기사
\t- ［IT조선］ [제목 하나](https://it.chosun.com/a/1)
\t- ［전자신문］ [제목 둘](https://www.etnews.com/b/2)
- **그 외** ([머니투데이](https://www.mt.co.kr/c/3))
일반 텍스트 (괄호) 는 URL 아님
"""


def test_extract_urls():
    """마크다운 링크의 URL만 추출, 일반 괄호는 무시."""
    urls = extract_urls(SAMPLE_MD)
    assert urls == {
        "https://it.chosun.com/a/1",
        "https://www.etnews.com/b/2",
        "https://www.mt.co.kr/c/3",
    }, urls


def test_load_backup_urls_merges_same_date():
    """같은 날짜의 suffix 파일(empty, raw_analysis 등)은 합집합으로 합쳐진다."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        (p / "2026-06-08.md").write_text("[a](https://x.com/1)", encoding="utf-8")
        (p / "2026-06-08_empty.md").write_text("[b](https://x.com/2)", encoding="utf-8")
        (p / "2026-06-09.md").write_text("[c](https://x.com/3)", encoding="utf-8")
        (p / "잡파일.txt").write_text("[d](https://x.com/4)", encoding="utf-8")
        by_date = load_backup_urls(p)
    assert by_date == {
        "2026-06-08": {"https://x.com/1", "https://x.com/2"},
        "2026-06-09": {"https://x.com/3"},
    }, by_date


def test_cross_date_overlaps():
    """인접 날짜 쌍의 재등장 수·비율·샘플 집계."""
    by_date = {
        "2026-06-07": {"u1", "u2", "u3"},
        "2026-06-08": {"u2", "u3", "u4", "u5"},   # 07과 2건 겹침
        "2026-06-09": {"u9"},                     # 08과 겹침 없음
    }
    rows = cross_date_overlaps(by_date)
    assert len(rows) == 2
    prev, cur, n, ratio, sample = rows[0]
    assert (prev, cur, n) == ("2026-06-07", "2026-06-08", 2)
    assert abs(ratio - 0.5) < 1e-9, ratio       # 4건 중 2건 재등장
    assert sample == ["u2", "u3"]
    assert rows[1][2] == 0 and rows[1][3] == 0.0


def test_cross_date_empty_day_safe():
    """수집 0건 날(빈 집합)이 있어도 division error 없이 비율 0."""
    by_date = {"2026-06-07": {"u1"}, "2026-06-08": set()}
    rows = cross_date_overlaps(by_date)
    assert rows[0][2] == 0 and rows[0][3] == 0.0


if __name__ == "__main__":
    failures = 0
    tests = [
        test_extract_urls,
        test_load_backup_urls_merges_same_date,
        test_cross_date_overlaps,
        test_cross_date_empty_day_safe,
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
