"""제목 대괄호 → 출처 링크 소실 결함 회귀 테스트.

배경 (ADR-002): 한국 기사 제목의 '[속보]', '[단독]'이 [text](url) 링크 텍스트에
들어가면 notion_writer 인라인 파서가 링크 매칭에 실패해 출처 링크가 통째로 소실됐다.
renderer._sanitize_link_text가 제목 대괄호를 전각으로 치환해 막는다.

이 테스트는 renderer 출력 → notion_writer parse_inline을 실제로 통과시켜
'제목 링크가 올바른 URL로 살아나는가'를 검증한다 (end-to-end 파서 검증).

실행: python -m pytest tests/test_link_sanitize.py -v
   또는 python tests/test_link_sanitize.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from renderer import _sanitize_link_text
from notion_writer import parse_inline


def _link_runs(line: str):
    """parse_inline 결과에서 link가 달린 run만 (content, url) 튜플로."""
    runs = parse_inline(line)
    out = []
    for r in runs:
        link = r.get("text", {}).get("link")
        if link:
            out.append((r["text"]["content"], link["url"]))
    return out


def _render_source_line(name: str, title: str, url: str) -> str:
    """report.md.j2:72의 출처 줄 형식 재현: - [name] [title](url)"""
    safe_title = _sanitize_link_text(title)
    return f"- [{name}] [{safe_title}]({url})"


# ----------------------------------------------------------------------
# 결함 재현 케이스 (수정 전이라면 실패해야 하는 케이스들)
# ----------------------------------------------------------------------
URL = "https://www.mk.co.kr/news/society/12058384"

CASES_BRACKET_TITLE = [
    ("머니투데이", "[속보] 검찰, 서소문 고가 붕괴 전담수사팀", URL),
    ("머니투데이", "[속보]서소문 붕괴 여파...123개 KTX 운행 중지", URL),
    ("동아일보", "[단독] 8000t급 한국형 핵잠, 2030년대 중반 첫 진수", URL),
    ("경향신문", "[종합 2보] 북한 미사일 발사", URL),
    ("한겨레", "[단독][르포] 현장을 가다", URL),  # 대괄호 2쌍
]

# 정상 케이스 (대괄호 없는 제목) — 회귀 방지
CASES_NORMAL = [
    ("IT조선", "삼성전자 HBM 양산 돌입", "https://example.com/1"),
    ("전자신문", "AI 반도체 수요 급증", "https://example.com/2"),
]


def test_bracket_titles_keep_link():
    """대괄호 제목도 제목 링크가 정확한 URL로 살아나야 한다."""
    for name, title, url in CASES_BRACKET_TITLE:
        line = _render_source_line(name, title, url)
        links = _link_runs(line)
        urls = [u for _, u in links]
        assert url in urls, (
            f"제목 링크 소실: title={title!r}\n"
            f"  생성된 줄: {line}\n"
            f"  링크 runs: {links}"
        )
        # 정확히 1개의 제목 링크 (매체명은 링크 아님)
        title_links = [(t, u) for t, u in links if u == url]
        assert len(title_links) == 1, (
            f"제목 링크가 1개가 아님: {title_links}\n  줄: {line}"
        )


def test_normal_titles_unchanged():
    """대괄호 없는 제목은 기존 동작 그대로 (회귀 방지)."""
    for name, title, url in CASES_NORMAL:
        line = _render_source_line(name, title, url)
        links = _link_runs(line)
        urls = [u for _, u in links]
        assert url in urls, f"정상 케이스 링크 소실: {line} -> {links}"
        # 제목이 변형되지 않았는지 (전각 치환은 대괄호 있을 때만)
        assert "［" not in line, f"대괄호 없는 제목에 전각 치환 오작동: {line}"


def test_sanitize_only_brackets():
    """_sanitize_link_text는 대괄호만 치환, 나머지 문자는 보존."""
    assert _sanitize_link_text("[속보] 삼성") == "［속보］ 삼성"
    assert _sanitize_link_text("대괄호 없음") == "대괄호 없음"
    assert _sanitize_link_text("[단독][르포]") == "［단독］［르포］"
    # URL 안전성: 별표 등 다른 문자는 안 건드림 (이번 결함 범위 밖)
    assert _sanitize_link_text("삼성*전자") == "삼성*전자"


if __name__ == "__main__":
    failures = 0
    for fn in [test_bracket_titles_keep_link, test_normal_titles_unchanged, test_sanitize_only_brackets]:
        try:
            fn()
            print(f"  PASS: {fn.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL: {fn.__name__}\n    {e}")
    if failures:
        print(f"\n{failures}개 실패")
        sys.exit(1)
    print("\n전체 통과")