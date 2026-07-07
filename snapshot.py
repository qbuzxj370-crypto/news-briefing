"""수집·분류 스냅샷 저장/복원 — 재실행 시 표본 보존.

배경 (2026-06-11 운영 관찰): 1차 실행(07:00)이 분석 단계에서 실패(Gemini 503,
폴백 키 부재)한 뒤 오후에 재실행하자, RSS 피드의 top-N이 이미 갱신되어
아침 표본을 잃었다 — 수집 314건 → 127건, 5개 매체(IT조선·전자신문·
머니투데이·매경·한겨레) 통째 누락. 윈도우는 고정(ADR-015)이지만 피드는
최신순 슬라이딩 버퍼라서, 늦은 재실행은 같은 윈도우라도 같은 표본을
보장하지 못한다.

대응: 수집+분류가 끝난 시점의 기사 상태(분야 포함)를 날짜별 JSON으로
backups/에 저장한다. 재실행 시 같은 대상 날짜의 스냅샷이 있으면 수집·분류를
건너뛰고 그 표본으로 분석부터 재개한다 — 1차 실행과 동일한 표본 보장 +
분류 LLM 호출 비용 절약. Actions에서는 백업 아티팩트(backups/)에 포함되며,
daily.yml의 복원 스텝이 직전 실행의 아티팩트에서 *_snapshot.json만 가져온다.

본문(body)은 저장하지 않는다 (ADR-019): 공개 저장소의 아티팩트는 누구나
다운로드할 수 있어, 기사 전문을 담으면 저작물 공중 전송이 된다. 스냅샷의
핵심 가치(표본 보존 + 분류 LLM 절약)는 제목·링크·분야만으로 성립하고,
본문은 재실행 시 선별 후보만 재크롤링(ADR-018, robots 준수)으로 복구한다.
구버전 스냅샷의 body 필드는 로드 시 무시된다.

스냅샷 파일명: backups/{date}_snapshot.json (날짜가 키 — 다른 날짜 것은 무시)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from collectors import Article
from notion_writer import BACKUP_DIR

# body는 의도적으로 제외 (ADR-019 — 공개 아티팩트에 기사 전문 미포함)
_FIELDS = (
    "title", "summary", "link", "source", "category", "is_cross_category",
)


def snapshot_path(date_str: str, backup_dir: Optional[Path] = None) -> Path:
    return (backup_dir or BACKUP_DIR) / f"{date_str}_snapshot.json"


def save_snapshot(
    date_str: str,
    articles: List[Article],
    backup_dir: Optional[Path] = None,
) -> Path:
    """수집+분류 완료 상태의 기사들을 JSON으로 저장."""
    base = backup_dir or BACKUP_DIR
    base.mkdir(exist_ok=True)
    # 분류 완전성 (ADR-017 1a): 미해결(분류 실패) 기사가 하나도 없으면 True.
    # 권위는 기사별 classification_unresolved 플래그이며, 이 top-level 값은
    # 편의/디버그용 denormalize. 재실행 회복(2a) 판정은 플래그로 한다.
    complete = not any(a.classification_unresolved for a in articles)
    payload = {
        "date_str": date_str,
        "created_at": datetime.now().astimezone().isoformat(),
        "complete": complete,
        "articles": [
            {**{f: getattr(a, f) for f in _FIELDS},
             "published": a.published.isoformat(),
             "classification_unresolved": a.classification_unresolved}
            for a in articles
        ],
    }
    path = snapshot_path(date_str, base)
    path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return path


def load_snapshot(
    date_str: str,
    backup_dir: Optional[Path] = None,
) -> Optional[List[Article]]:
    """같은 대상 날짜의 스냅샷이 있으면 기사 리스트 복원. 없거나 손상이면 None.

    None 반환 시 호출자는 평소처럼 신규 수집으로 진행한다 — 스냅샷은
    최적화일 뿐 실패해도 파이프라인이 멈추면 안 된다 (graceful 원칙).
    """
    path = snapshot_path(date_str, backup_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("date_str") != date_str:
            return None
        articles = []
        for d in payload["articles"]:
            articles.append(Article(
                published=datetime.fromisoformat(d["published"]),
                # 구버전 스냅샷엔 없는 필드 — graceful 기본값 (해결됨으로 간주)
                classification_unresolved=d.get("classification_unresolved", False),
                **{f: d[f] for f in _FIELDS},
            ))
        return articles or None
    except Exception as e:
        print(f"  [경고] 스냅샷 복원 실패 ({path.name}): {e} — 신규 수집으로 진행")
        return None
