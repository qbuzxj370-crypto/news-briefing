"""마크다운 → Notion 블록 변환 + DB row 생성 + 실패 시 로컬 백업.

지원 블록:
- heading_1/2/3 (#, ##, ###)
- toggle heading_3 (마커: "▶ ### 제목")
- callout (마커: "> 💡 **헤더**\n> 내용...")
- bulleted_list_item (- 또는 *)
- numbered_list_item (1. 2. 3.)
- paragraph
- divider (---)
- quote (>)

지원 인라인 (rich_text):
- **굵게** → annotations.bold
- *기울임* → annotations.italic
- [텍스트](URL) → text.link
- 일반 텍스트

청킹: Notion API는 children 한 번에 100블록 제한. 초과 시 분할 업로드.
백업: 어떤 실패든 로컬 .md로 저장.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from notion_client import Client


NOTION_CHILDREN_LIMIT = 100      # 한 번에 보낼 수 있는 children 개수
NOTION_TEXT_CHUNK = 1900         # 한 rich_text 객체당 텍스트 길이 한도 (Notion은 2000)
BACKUP_DIR = Path("backups")


# ----------------------------------------------------------------------
# 인라인 rich_text 파서
# ----------------------------------------------------------------------
# **굵게**, *기울임*, [텍스트](URL)을 동시에 감지
# 주의: **를 *보다 먼저 매치해야 함 (그래서 ** 패턴이 먼저 옴)
_INLINE_PATTERN = re.compile(
    r"(\*\*([^*]+?)\*\*)"               # group 1, 2: **bold**
    r"|(\*([^*]+?)\*)"                  # group 3, 4: *italic*
    r"|(\[([^\]]+)\]\(([^)]+)\))"       # group 5, 6, 7: [text](url)
)


def _text_run(content: str, bold: bool = False, italic: bool = False, link: Optional[str] = None) -> Dict[str, Any]:
    """Notion rich_text 객체 1개 생성. 2000자 초과는 호출 전 split 책임."""
    text_obj: Dict[str, Any] = {"content": content}
    if link:
        text_obj["link"] = {"url": link}
    return {
        "type": "text",
        "text": text_obj,
        "annotations": {
            "bold": bold,
            "italic": italic,
            "strikethrough": False,
            "underline": False,
            "code": False,
            "color": "default",
        },
    }


def _split_long_text(content: str) -> List[str]:
    """긴 텍스트를 Notion 한도 이하로 분할."""
    if len(content) <= NOTION_TEXT_CHUNK:
        return [content]
    parts = []
    for i in range(0, len(content), NOTION_TEXT_CHUNK):
        parts.append(content[i:i + NOTION_TEXT_CHUNK])
    return parts


def parse_inline(text: str) -> List[Dict[str, Any]]:
    """한 줄의 마크다운 인라인 → rich_text 배열."""
    runs: List[Dict[str, Any]] = []
    pos = 0

    for m in _INLINE_PATTERN.finditer(text):
        # 매치 전 일반 텍스트
        if m.start() > pos:
            plain = text[pos:m.start()]
            for chunk in _split_long_text(plain):
                if chunk:
                    runs.append(_text_run(chunk))

        if m.group(1):  # **bold**
            content = m.group(2)
            for chunk in _split_long_text(content):
                runs.append(_text_run(chunk, bold=True))
        elif m.group(3):  # *italic*
            content = m.group(4)
            for chunk in _split_long_text(content):
                runs.append(_text_run(chunk, italic=True))
        elif m.group(5):  # [text](url)
            link_text = m.group(6)
            link_url = m.group(7)
            for chunk in _split_long_text(link_text):
                runs.append(_text_run(chunk, link=link_url))

        pos = m.end()

    # 마지막 매치 이후 잔여 텍스트
    if pos < len(text):
        tail = text[pos:]
        for chunk in _split_long_text(tail):
            if chunk:
                runs.append(_text_run(chunk))

    # 빈 입력 보호
    if not runs:
        runs = [_text_run("")]

    return runs


# ----------------------------------------------------------------------
# 블록 빌더 헬퍼
# ----------------------------------------------------------------------
def _block(type_name: str, content: Dict[str, Any]) -> Dict[str, Any]:
    return {"object": "block", "type": type_name, type_name: content}


def _heading(level: int, text: str, toggleable: bool = False, children: Optional[List[Dict]] = None) -> Dict:
    key = f"heading_{level}"
    payload: Dict[str, Any] = {
        "rich_text": parse_inline(text),
        "is_toggleable": toggleable,
    }
    if toggleable and children:
        payload["children"] = children
    return _block(key, payload)


def _paragraph(text: str) -> Dict:
    return _block("paragraph", {"rich_text": parse_inline(text)})


def _bullet(text: str) -> Dict:
    return _block("bulleted_list_item", {"rich_text": parse_inline(text)})


def _divider() -> Dict:
    return _block("divider", {})


def _callout(text_lines: List[str], emoji: str = "💡") -> Dict:
    """여러 줄을 rich_text로 합쳐 callout 1개 생성."""
    joined = "\n".join(text_lines)
    return _block("callout", {
        "rich_text": parse_inline(joined),
        "icon": {"type": "emoji", "emoji": emoji},
        "color": "gray_background",
    })


# ----------------------------------------------------------------------
# 메인 파서: 마크다운 라인들 → Notion 블록 배열
# ----------------------------------------------------------------------
TOGGLE_HEADING_RE = re.compile(r"^▶\s*(#{1,3})\s+(.+)$")
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")
BULLET_RE = re.compile(r"^(\s*)-\s+(.+)$")
CALLOUT_LINE_RE = re.compile(r"^>\s?(.*)$")


def parse_markdown_to_blocks(md: str) -> List[Dict[str, Any]]:
    """마크다운 문자열 → Notion 블록 배열.
    
    토글 헤더는 그 다음에 오는 인덴트된 줄들을 children으로 묶음.
    콜아웃은 연속된 ">" 줄들을 하나로 묶음.
    """
    lines = md.split("\n")
    blocks: List[Dict[str, Any]] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 빈 줄 스킵
        if not stripped:
            i += 1
            continue

        # 구분선
        if stripped == "---":
            blocks.append(_divider())
            i += 1
            continue

        # 콜아웃 (> 로 시작하는 연속 줄들)
        if stripped.startswith(">"):
            callout_lines = []
            while i < n and lines[i].strip().startswith(">"):
                m = CALLOUT_LINE_RE.match(lines[i].strip())
                callout_lines.append(m.group(1) if m else "")
                i += 1
            # 첫 줄에서 이모지 힌트 추출 (💡 ⚠️ 등)
            first = callout_lines[0] if callout_lines else ""
            emoji = "💡"
            for em in ("💡", "⚠️", "📌", "ℹ️", "✅", "🚨"):
                if em in first:
                    emoji = em
                    # icon으로 쓸 거니까 본문 첫 줄에서는 제거
                    callout_lines[0] = first.replace(em, "", 1).lstrip()
                    break
            blocks.append(_callout(callout_lines, emoji=emoji))
            continue

        # 토글 헤더 (▶ ### 제목)
        m_toggle = TOGGLE_HEADING_RE.match(stripped)
        if m_toggle:
            level = len(m_toggle.group(1))
            title = m_toggle.group(2)
            # 다음 줄부터 인덴트(3칸 이상 공백)된 줄들을 children으로
            i += 1
            children_lines: List[str] = []
            while i < n:
                nxt = lines[i]
                if nxt.startswith("   ") or not nxt.strip():
                    # 인덴트 한 단계 제거 후 수집 (앞 3칸만 제거)
                    if nxt.startswith("   "):
                        children_lines.append(nxt[3:])
                    else:
                        children_lines.append("")
                    i += 1
                else:
                    break
            # 트레일링 빈 줄 정리
            while children_lines and not children_lines[-1].strip():
                children_lines.pop()
            children_md = "\n".join(children_lines)
            child_blocks = parse_markdown_to_blocks(children_md) if children_md else []
            blocks.append(_heading(level, title, toggleable=True, children=child_blocks))
            continue

        # 일반 헤더
        m_h = HEADING_RE.match(stripped)
        if m_h:
            level = len(m_h.group(1))
            title = m_h.group(2)
            blocks.append(_heading(level, title))
            i += 1
            continue

        # 불릿
        m_b = BULLET_RE.match(line)
        if m_b:
            # 불릿의 들여쓰기 처리는 단순화: 같은 줄 텍스트만 사용
            # (현재 템플릿은 중첩 불릿을 만들지 않음)
            blocks.append(_bullet(m_b.group(2).strip()))
            # 다음 줄이 들여쓰기된 연속 줄이면 paragraph로 children 추가
            i += 1
            cont_lines = []
            while i < n and lines[i].startswith("  ") and not BULLET_RE.match(lines[i]) and lines[i].strip():
                cont_lines.append(lines[i].strip())
                i += 1
            if cont_lines:
                # children paragraph 추가
                last_block = blocks[-1]
                last_block["bulleted_list_item"]["children"] = [
                    _paragraph(" ".join(cont_lines))
                ]
            continue

        # 그 외: paragraph
        blocks.append(_paragraph(stripped))
        i += 1

    return blocks


# ----------------------------------------------------------------------
# Notion 페이지 생성 (DB row)
# ----------------------------------------------------------------------
def _chunked(seq: List[Any], size: int) -> List[List[Any]]:
    return [seq[i:i + size] for i in range(0, len(seq), size)]


def _get_data_source_info(client: Client, database_id: str) -> Tuple[str, str]:
    """DB의 첫 data_source ID와 title 속성명을 반환.
    
    2025-09-03 Notion API부터 properties는 data_source 안에 있음.
    """
    db = client.databases.retrieve(database_id)
    data_sources = db.get("data_sources", []) if isinstance(db, dict) else []
    if not data_sources:
        raise RuntimeError(
            f"DB에 data_source가 없습니다. DB 응답 키: {list(db.keys()) if isinstance(db, dict) else type(db)}"
        )

    data_source_id = data_sources[0]["id"]
    print(f"  data_source 감지: {data_sources[0].get('name', '')} ({data_source_id})")

    # data_source의 properties 조회
    ds = client.request(path=f"data_sources/{data_source_id}", method="GET")
    props = ds.get("properties", {}) if isinstance(ds, dict) else {}
    title_prop = "Name"
    for prop_name, prop_config in props.items():
        if isinstance(prop_config, dict) and prop_config.get("type") == "title":
            title_prop = prop_name
            break
    print(f"  title 속성명: '{title_prop}'")
    return data_source_id, title_prop


def create_page(
    client: Client,
    database_id: str,
    title: str,
    date_iso: str,
    model: str,
    body_ratio_pct: int,
    category_tags: List[str],
    article_count: int,
    blocks: List[Dict[str, Any]],
) -> str:
    """DB row 생성 후 children 청킹으로 추가. 페이지 ID 반환.

    2025-09-03 API 기준: parent로 data_source_id 사용.
    """
    data_source_id, title_prop = _get_data_source_info(client, database_id)
    properties = {
        title_prop: {"title": [{"type": "text", "text": {"content": title}}]},
        "날짜": {"date": {"start": date_iso}},
        "모델": {"select": {"name": model}},
        "본문 확보율": {"number": body_ratio_pct / 100.0},
        "분야 태그": {"multi_select": [{"name": t} for t in category_tags]},
        "분석 기사 수": {"number": article_count},
    }

    # 최초 children은 100개까지만 같이 전송
    first_batch = blocks[:NOTION_CHILDREN_LIMIT]
    rest = blocks[NOTION_CHILDREN_LIMIT:]

    page = client.pages.create(
        parent={"type": "data_source_id", "data_source_id": data_source_id},
        properties=properties,
        children=first_batch,
    )
    page_id = page["id"]

    # 나머지 블록은 children.append로 청킹 업로드
    for chunk in _chunked(rest, NOTION_CHILDREN_LIMIT):
        client.blocks.children.append(block_id=page_id, children=chunk)

    return page_id




# ----------------------------------------------------------------------
# 로컬 백업
# ----------------------------------------------------------------------
def save_local_backup(markdown: str, date_str: str, suffix: str = "") -> Path:
    """로컬 .md 파일로 백업. GitHub Actions에서는 아티팩트로 보존됨."""
    BACKUP_DIR.mkdir(exist_ok=True)
    name = f"{date_str}{('_' + suffix) if suffix else ''}.md"
    path = BACKUP_DIR / name
    path.write_text(markdown, encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# 엔트리포인트: 마크다운 + 메타데이터 → Notion 업로드
# ----------------------------------------------------------------------
def _fetch_issue_block_urls(client: Client, page_id: str) -> Dict[str, str]:
    """페이지의 deep_issue 헤딩 블록을 조회해 {이슈제목: Notion딥링크} 반환.

    deep_issue 헤딩은 heading_3 + is_toggleable=True + "📊 제목" 형식.
    minor_issues 그룹 헤딩("📌 그 외 이슈")은 제외.
    페이지 블록이 100개를 초과해도 커서 페이지네이션으로 전체 탐색.
    실패 시 빈 dict 반환 (Slack에서 링크 없이 제목만 표시하는 폴백).
    """
    issue_urls: Dict[str, str] = {}
    page_clean = page_id.replace("-", "")
    cursor: Optional[str] = None

    while True:
        kwargs: Dict[str, Any] = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.blocks.children.list(**kwargs)

        for block in resp.get("results", []):
            if block.get("type") != "heading_3":
                continue
            h3 = block.get("heading_3", {})
            if not h3.get("is_toggleable"):
                continue
            full_text = "".join(
                rt.get("text", {}).get("content", "")
                for rt in h3.get("rich_text", [])
            ).strip()
            # "📊 제목" 형식만 수집 (minor 그룹 헤딩 "📌 그 외 이슈" 제외)
            if not full_text.startswith("📊"):
                continue
            title = full_text[1:].strip()  # 이모지 + 공백 제거
            block_clean = block["id"].replace("-", "")
            issue_urls[title] = f"https://www.notion.so/{page_clean}#{block_clean}"

        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    return issue_urls


def upload_to_notion(
    markdown: str,
    date_str: str,
    model: str,
    body_ratio_pct: int,
    category_tags: List[str],
    article_count: int,
) -> Tuple[bool, str, Dict[str, str]]:
    """마크다운을 Notion DB에 업로드.

    반환: (성공 여부, 페이지 ID 또는 백업 경로, {이슈제목: Notion딥링크})
    실패 시 로컬 백업으로 폴백. 딥링크 맵은 실패 시 빈 dict.
    """
    notion_key = os.environ.get("NOTION_API_KEY")
    db_id = os.environ.get("NOTION_DATABASE_ID")

    # 1) 어떤 경우든 로컬 백업 먼저 저장 (보험)
    backup_path = save_local_backup(markdown, date_str)
    print(f"  로컬 백업: {backup_path}")

    if not notion_key or not db_id:
        print("  [경고] NOTION_API_KEY 또는 NOTION_DATABASE_ID 미설정. 백업만 저장.")
        return False, str(backup_path), {}

    try:
        blocks = parse_markdown_to_blocks(markdown)
        print(f"  Notion 블록 {len(blocks)}개 생성")

        client = Client(auth=notion_key, notion_version="2025-09-03")
        title = f"{date_str} 데일리 브리핑"
        page_id = create_page(
            client=client,
            database_id=db_id,
            title=title,
            date_iso=date_str,
            model=model,
            body_ratio_pct=body_ratio_pct,
            category_tags=category_tags,
            article_count=article_count,
            blocks=blocks,
        )
        print(f"  ✓ Notion 페이지 생성: {page_id}")

        # deep_issue 헤딩 블록 ID 조회 → Slack 딥링크용
        try:
            issue_block_map = _fetch_issue_block_urls(client, page_id)
            print(f"  ✓ 이슈 딥링크 {len(issue_block_map)}개 수집")
        except Exception as e:
            print(f"  [경고] 이슈 딥링크 수집 실패 (무시하고 진행): {e}")
            issue_block_map = {}

        return True, page_id, issue_block_map

    except Exception as e:
        import traceback
        print(f"  [실패] Notion 업로드 실패: {type(e).__name__}: {e}")
        traceback.print_exc()
        print(f"          로컬 백업 사용: {backup_path}")
        return False, str(backup_path), {}


# ----------------------------------------------------------------------
# 단독 실행 (파서 검증)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    sample = Path(__file__).parent.parent / "outputs" / "sample_output.md"
    if not sample.exists():
        sample = Path("/mnt/user-data/outputs/sample_output.md")
    if sample.exists():
        md = sample.read_text(encoding="utf-8")
    else:
        md = """# 테스트 페이지

> 💡 **오늘의 핵심**
> 첫째 줄
> 둘째 줄

---

## 분야

### 🎯 핵심
- 항목 1
- 항목 2

▶ ### 📊 토글 헤더
   - **굵게** 내용
   - [링크](https://example.com)

▶ ### 📌 그 외 이슈 (2건)
   - **이슈 A** ([매체](URL))
     설명
"""
    blocks = parse_markdown_to_blocks(md)
    import json
    print(f"총 {len(blocks)}개 블록 생성")
    print(json.dumps(blocks[:8], ensure_ascii=False, indent=2))
