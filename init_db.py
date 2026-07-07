"""Notion DB 초기 생성 스크립트.

최초 1회만 실행. 부모 페이지 하위에 데일리 브리핑 DB를 생성하고
생성된 DB ID를 출력한다. 그 ID를 NOTION_DATABASE_ID 환경변수로 저장하면 됨.

사용법:
    export NOTION_API_KEY=secret_xxx
    export NOTION_PARENT_PAGE_ID=xxxxxxxxxxxx
    python init_db.py
"""

from __future__ import annotations

import os
import sys

from notion_client import Client


DB_TITLE = "데일리 뉴스 브리핑"
DB_DESCRIPTION = "매일 아침 자동 생성되는 분야별 뉴스 분석 보고서"

# 분야 옵션 (page_mockup.md의 5분야와 일치)
CATEGORY_OPTIONS = [
    {"name": "IT·테크·AI", "color": "blue"},
    {"name": "경제·금융·증시", "color": "green"},
    {"name": "정치", "color": "red"},
    {"name": "사회·시사", "color": "orange"},
    {"name": "산업", "color": "purple"},
]

MODEL_OPTIONS = [
    {"name": "gemini-2.5-flash", "color": "blue"},
    {"name": "gpt-5-mini", "color": "green"},
    {"name": "none", "color": "gray"},
]


def create_database(parent_page_id: str) -> str:
    """DB 생성. 생성된 database_id 반환."""
    client = Client(auth=os.environ["NOTION_API_KEY"], notion_version="2025-09-03")

    properties = {
        # 제목 — Notion DB의 기본 Title 속성. "Name"이 Notion 기본 이름.
        "제목": {"title": {}},
        # 날짜
        "날짜": {"date": {}},
        # 모델 (Select)
        "모델": {
            "select": {"options": MODEL_OPTIONS}
        },
        # 본문 확보율 (%)
        "본문 확보율": {
            "number": {"format": "percent"}
        },
        # 분야 태그 (Multi-select)
        "분야 태그": {
            "multi_select": {"options": CATEGORY_OPTIONS}
        },
        # 분석 기사 수
        "분석 기사 수": {"number": {"format": "number"}},
        # 생성 시각은 Notion의 기본 created_time 속성으로 자동 추가됨 — 명시 불필요
    }

    response = client.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": DB_TITLE}}],
        description=[{"type": "text", "text": {"content": DB_DESCRIPTION}}],
        properties=properties,
    )

    return response["id"]


def main() -> int:
    notion_key = os.environ.get("NOTION_API_KEY")
    parent_id = os.environ.get("NOTION_PARENT_PAGE_ID")

    if not notion_key:
        print("[오류] NOTION_API_KEY 환경변수가 필요합니다.")
        return 1
    if not parent_id:
        print("[오류] NOTION_PARENT_PAGE_ID 환경변수가 필요합니다.")
        print("       Notion에서 부모 페이지를 만들고 'Integration 연결' 후 페이지 ID를 사용하세요.")
        return 1

    print(f"[시작] '{DB_TITLE}' DB를 페이지 {parent_id} 하위에 생성합니다...")
    try:
        db_id = create_database(parent_id)
    except Exception as e:
        print(f"[실패] DB 생성 실패: {e}")
        print("       부모 페이지에 Integration이 연결되어 있는지 확인하세요.")
        return 1

    # ID 정리 (Notion은 dashes 포함/미포함 둘 다 받음)
    db_id_clean = db_id.replace("-", "")

    print(f"\n[성공] DB 생성 완료")
    print(f"   DB ID (dashed):  {db_id}")
    print(f"   DB ID (compact): {db_id_clean}")
    print(f"\n다음 환경변수를 설정하세요:")
    print(f"   NOTION_DATABASE_ID={db_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
