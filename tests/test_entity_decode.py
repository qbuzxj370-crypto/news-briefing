"""RSS 제목/요약 정제(HTML 엔티티 디코딩 + zero-width 제거) 회귀 테스트.

배경 (2026-06-03 운영): RSS 제목의 HTML 엔티티(&#039; &quot; &#8593; 등)가
디코딩 없이 그대로 출력됨. 산출물에 `&#039;초박빙&#039;`, `&quot;꼰대세요?&quot;`
형태로 노출되어 신뢰도 저하.

원인: collectors.py가 entry.get("title")을 그대로 사용, html.unescape 누락.
대응: 제목·요약을 html.unescape로 디코딩. dedup 전에 수행(엔티티 형태와
디코딩 형태가 다른 제목으로 취급되는 것 방지). summary는 태그 제거 후 디코딩
(순서 반대면 &lt;tag&gt;가 디코딩→태그제거로 삭제되어 데이터 손실).

추가 (2026-06-10): 제목 선두의 zero-width 문자(U+FEFF 등)가 Notion 링크
텍스트에 그대로 노출된 사례(06-04·06-09 산출물) 대응 — collectors의 정제
로직이 clean_feed_title/clean_feed_summary 헬퍼로 추출되어, 이 테스트는
재현 복제본이 아니라 실제 함수를 직접 검증한다.

실행: python tests/test_entity_decode.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import clean_feed_title as _decode_title
from collectors import clean_feed_summary as _decode_summary


def test_decode_real_entities():
    """06-03 산출물에 실제로 나온 엔티티들이 디코딩되는지."""
    cases = [
        ("&#039;초박빙&#039; 서울시장 선거", "'초박빙' 서울시장 선거"),
        ('&quot;꼰대세요?&quot; 적반하장', '"꼰대세요?" 적반하장'),
        ("&#039;용지 부족&#039; 잠실7동", "'용지 부족' 잠실7동"),
        ("4년 전보다 0.7%P&#8593;", "4년 전보다 0.7%P↑"),
        ("&quot;공장 터뜨려놓고&quot;", '"공장 터뜨려놓고"'),
    ]
    for raw, expected in cases:
        got = _decode_title(raw)
        assert got == expected, f"{raw!r} → {got!r}, 기대 {expected!r}"


def test_no_entity_remains():
    """디코딩 후 &#, &quot 등 엔티티 패턴이 남지 않는지."""
    raw = "&#039;테스트&#039; &quot;인용&quot; &amp; &lt;꺾쇠&gt;"
    got = _decode_title(raw)
    # 일반적인 엔티티 잔존 패턴 검사
    assert "&#0" not in got
    assert "&quot;" not in got
    assert "&amp;" not in got


def test_summary_order_strip_then_unescape():
    """summary는 태그 제거 후 디코딩 — &lt;tag&gt; 텍스트가 보존되는지.

    순서가 반대면(디코딩 먼저) &lt;script&gt;→<script>로 풀린 뒤 태그 제거
    정규식이 삭제해 데이터 손실. 텍스트로 쓰인 꺾쇠는 보존되어야 함.
    """
    raw = "코드에서 &lt;script&gt; 태그를 &#039;제거&#039;합니다"
    got = _decode_summary(raw)
    assert "<script>" in got, f"꺾쇠 텍스트가 보존돼야 함: {got!r}"
    assert "'제거'" in got


def test_real_html_tags_still_removed():
    """실제 HTML 태그(엔티티 아닌)는 여전히 제거되는지."""
    raw = "<p>본문 <b>강조</b> 텍스트</p>"
    got = _decode_summary(raw)
    assert got == "본문 강조 텍스트", f"태그 제거 실패: {got!r}"


def test_decode_then_dedup_consistent():
    """디코딩 후 dedup — 엔티티 형태와 디코딩 형태가 같은 제목으로 합쳐지는지.

    같은 기사가 한 피드엔 &#039; 다른 피드엔 ' 로 올 경우, 디코딩 후엔
    동일 제목이 되어 dedup으로 합쳐져야 한다.
    """
    t1 = _decode_title("&#039;속보&#039; 사건 발생")
    t2 = _decode_title("'속보' 사건 발생")
    assert t1 == t2, f"디코딩 후 동일해야 dedup됨: {t1!r} vs {t2!r}"


def test_no_double_unescape_damage():
    """이미 디코딩된 정상 텍스트를 다시 디코딩해도 망가지지 않는지.

    &가 포함된 정상 제목(예: 'AT&T')이 깨지지 않아야 함.
    """
    # 이미 정상인 텍스트
    raw = "삼성 & LG 협력"
    got = _decode_title(raw)
    assert got == "삼성 & LG 협력", f"정상 & 가 보존돼야 함: {got!r}"


def test_zero_width_removed():
    """제목/요약의 zero-width 문자가 제거되는지 (06-04·06-09 실물 사례).

    IT조선 일부 제목 선두에 U+FEFF가 붙어 '[﻿AI 모델이...' 형태로
    Notion 링크 텍스트에 노출됐다. 중간에 끼인 ZWSP 등도 함께 제거.
    """
    cases = [
        # 06-09 실물: 제목 선두 U+FEFF
        ("﻿AI 모델이 싸지는 시대, 돈은 클라우드에서 나온다",
         "AI 모델이 싸지는 시대, 돈은 클라우드에서 나온다"),
        # 06-04 실물: 제목 중간에도 U+FEFF
        ("﻿AI가 눈을 뜨고 ﻿［정원훈의 AI 트렌드］",
         "AI가 눈을 뜨고 ［정원훈의 AI 트렌드］"),
        # ZWSP(U+200B)/WORD JOINER(U+2060) 변형
        ("제​목 테⁠스트", "제목 테스트"),
    ]
    for raw, expected in cases:
        got = _decode_title(raw)
        assert got == expected, f"{raw!r} → {got!r}, 기대 {expected!r}"
    # summary 경로도 동일 적용
    got = _decode_summary("<p>﻿요약 본문</p>")
    assert got == "요약 본문", f"summary zero-width 잔존: {got!r}"


if __name__ == "__main__":
    failures = 0
    tests = [
        test_decode_real_entities,
        test_no_entity_remains,
        test_summary_order_strip_then_unescape,
        test_real_html_tags_still_removed,
        test_decode_then_dedup_consistent,
        test_no_double_unescape_damage,
        test_zero_width_removed,
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