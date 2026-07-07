"""날짜 간 중복 기사 빈도 측정 도구 (ADR-015 측정 계획).

48시간 수집 윈도우 때문에 어제 분석한 기사가 오늘 또 후보에 오를 수 있다.
ADR-015는 처리 방침을 설계로 확정하되 "실제 빈도 측정 후 구현 판단"으로
보류했다 — 이 스크립트가 그 측정 도구다.

backups/ 의 백업 마크다운에서 날짜별 기사 URL을 추출해, 인접 날짜 간
같은 URL이 재등장하는 빈도를 보고한다.

판단 기준 (ADR-015):
- 드물면(예: 5일에 1~2건) 배제 로직 구현은 ROI 낮음 → 보류 유지
- 잦으면 Notion 어제 페이지 역조회 기반 배제 구현 착수

실행: python dup_probe.py [backups 디렉토리, 기본 ./backups]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

# 백업 파일명: YYYY-MM-DD.md 또는 YYYY-MM-DD_suffix.md
BACKUP_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:_.*)?\.md$")
# 마크다운 링크의 URL 부분
URL_RE = re.compile(r"\]\((https?://[^)\s]+)\)")


def extract_urls(markdown: str) -> Set[str]:
    """백업 마크다운에서 기사 URL 집합 추출."""
    return set(URL_RE.findall(markdown))


def load_backup_urls(backup_dir: Path) -> Dict[str, Set[str]]:
    """backups/ 디렉토리에서 날짜 → URL 집합 매핑 구성.

    같은 날짜의 파일이 여러 개(suffix 포함)면 합집합으로 합친다.
    """
    by_date: Dict[str, Set[str]] = {}
    for f in sorted(backup_dir.glob("*.md")):
        m = BACKUP_NAME_RE.match(f.name)
        if not m:
            continue
        date = m.group(1)
        by_date.setdefault(date, set()).update(
            extract_urls(f.read_text(encoding="utf-8"))
        )
    return by_date


def cross_date_overlaps(
    by_date: Dict[str, Set[str]],
) -> List[Tuple[str, str, int, float, List[str]]]:
    """인접 날짜 쌍별 URL 재등장 통계.

    Returns:
        [(전날, 다음날, 중복 수, 다음날 대비 중복 비율, 중복 URL 샘플≤3), ...]
        날짜 오름차순. 빈 날짜 집합은 비율 0으로 처리.
    """
    dates = sorted(by_date)
    rows = []
    for prev, cur in zip(dates, dates[1:]):
        dup = by_date[prev] & by_date[cur]
        cur_total = len(by_date[cur])
        ratio = (len(dup) / cur_total) if cur_total else 0.0
        rows.append((prev, cur, len(dup), ratio, sorted(dup)[:3]))
    return rows


def main(backup_dir: Path) -> int:
    if not backup_dir.is_dir():
        print(f"백업 디렉토리 없음: {backup_dir}")
        print("(GitHub Actions 운영이면 artifact를 내려받아 지정: python dup_probe.py <경로>)")
        return 1

    by_date = load_backup_urls(backup_dir)
    if len(by_date) < 2:
        print(f"측정에는 2일 이상 필요 (현재 {len(by_date)}일: {sorted(by_date)})")
        return 1

    print(f"측정 대상: {len(by_date)}일 ({min(by_date)} ~ {max(by_date)})")
    print()
    total_dup = 0
    for prev, cur, n, ratio, sample in cross_date_overlaps(by_date):
        total_dup += n
        line = f"{prev} → {cur}: 재등장 {n}건 ({ratio:.0%})"
        print(line)
        for url in sample:
            print(f"    {url}")
    days = len(by_date) - 1
    print()
    print(f"합계: {days}일 전이에서 재등장 {total_dup}건 (일평균 {total_dup / days:.1f}건)")
    print("판단(ADR-015): 일평균 1건 미만이면 보류 유지, 그 이상이면 배제 구현 검토")
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "backups"
    sys.exit(main(target))
