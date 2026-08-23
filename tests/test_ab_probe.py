"""A/B 하네스 집계·비교 로직 테스트.

배경: 하네스의 목적은 "바꾼 게 효과가 있었나"를 측정으로 답하는 것이다. 집계가
틀리면 잘못된 판단을 내리게 되므로, LLM 호출 없이 검증 가능한 부분(지표 계산·
회차 집계·비교 출력)을 회귀로 고정한다.

ab_probe는 무거운 import를 전부 함수 안에 두므로 이 모듈 자체는 의존성 없이
로드된다. _metrics만 analyzer를 쓰므로 그 테스트는 스텁이 필요하다.

실행: python tests/test_ab_probe.py
"""

import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_probe


def _write_run(dirpath: Path, label: str, run: int, **metrics):
    """가짜 실행 결과 파일 생성 (compare 입력)."""
    base = {
        "insight_total": 5, "insight_with_number": 5, "insight_with_limitation": 5,
        "deep_issue_count": 15, "avg_refs": 2.0, "low_ref_long": 0,
        "single_source": 0, "confidence": {"높음": 5, "중간": 5, "판단유보": 5},
    }
    base.update(metrics)
    payload = {
        "label": label, "run": run, "date_str": "2026-07-25",
        "model": f"model-{label}", "model_used": f"model-{label}",
        "metrics": base,
        "analysis": {"categories": [{
            "name": "IT·테크·AI",
            "insight": {"observation": f"{label} 관찰", "interpretation": f"{label} 해석", "limitation": ""},
        }]},
    }
    (dirpath / f"2026-07-25_{label}_{run}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_agg_single_run_shows_raw_value():
    runs = [{"metrics": {"x": 3}}]
    assert ab_probe._agg(runs, "x") == "3"


def test_agg_identical_runs_omit_range():
    """회차 값이 같으면 범위를 표기하지 않는다 (노이즈 제거)."""
    runs = [{"metrics": {"x": 4}}, {"metrics": {"x": 4}}]
    assert ab_probe._agg(runs, "x") == "4.0"


def test_agg_spread_is_shown():
    """편차가 있으면 최소~최대를 함께 보여 단일 표본 결론을 막는다."""
    runs = [{"metrics": {"x": 2}}, {"metrics": {"x": 6}}]
    out = ab_probe._agg(runs, "x")
    assert out == "4.0 (2~6)", out


def test_agg_empty_is_safe():
    assert ab_probe._agg([], "x") == "-"


def test_compare_outputs_both_labels_and_warning():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        orig = ab_probe.AB_DIR
        ab_probe.AB_DIR = d
        try:
            _write_run(d, "base", 1, insight_with_number=2)
            _write_run(d, "base", 2, insight_with_number=4)
            _write_run(d, "v36", 1, insight_with_number=5)
            _write_run(d, "v36", 2, insight_with_number=5)
            buf = io.StringIO()
            with redirect_stdout(buf):
                ab_probe.compare(["base", "v36"], show_insight=False)
            out = buf.getvalue()
        finally:
            ab_probe.AB_DIR = orig

    assert "base: 2회" in out and "v36: 2회" in out, out
    # base는 회차 편차가 있으므로 범위가 표기되어야 한다
    assert "3.0 (2~5)" in out or "3.0 (2~4)" in out, out
    # 단일 표본 경고가 항상 붙는다
    assert "샘플링 편차" in out, out
    # 자동 판정 불가 항목을 명시한다
    assert "자동 판정이 불가능" in out, out


def test_compare_missing_label_fails_loudly():
    """결과가 없는 라벨을 조용히 건너뛰지 않는다."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        orig = ab_probe.AB_DIR
        ab_probe.AB_DIR = d
        try:
            _write_run(d, "base", 1)
            try:
                with redirect_stdout(io.StringIO()):
                    ab_probe.compare(["base", "없는라벨"], show_insight=False)
                assert False, "없는 라벨인데 통과했다"
            except SystemExit as e:
                assert "없는라벨" in str(e)
        finally:
            ab_probe.AB_DIR = orig


def test_compare_insight_flag_prints_bodies():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        orig = ab_probe.AB_DIR
        ab_probe.AB_DIR = d
        try:
            _write_run(d, "base", 1)
            buf = io.StringIO()
            with redirect_stdout(buf):
                ab_probe.compare(["base"], show_insight=True)
            out = buf.getvalue()
        finally:
            ab_probe.AB_DIR = orig
    assert "base 관찰" in out and "base 해석" in out, out
    # limitation이 비면 그 줄은 출력하지 않는다
    assert "한계:" not in out, out


def test_metrics_counts_from_analysis():
    """지표는 analysis에서 직접 센다 (compare가 id_to_article 없이 동작하도록)."""
    import types
    for name in ("feedparser", "trafilatura"):
        sys.modules.setdefault(name, types.ModuleType(name))
    bs4 = types.ModuleType("bs4"); bs4.BeautifulSoup = object
    sys.modules.setdefault("bs4", bs4)
    ten = types.ModuleType("tenacity")
    ten.Retrying = object
    for fn in ("retry_if_exception", "stop_after_attempt", "wait_exponential"):
        setattr(ten, fn, lambda *a, **k: None)
    sys.modules.setdefault("tenacity", ten)

    analysis = {"categories": [{
        "name": "IT·테크·AI",
        "insight": {"observation": "AI 18건", "interpretation": "해석", "limitation": "하루치"},
        "deep_issues": [
            {"title": "a", "confidence": "높음", "referenced_article_ids": [1, 2, 3],
             "context": "c", "implication": "i", "watch_points": "w"},
            {"title": "b", "confidence": "판단유보", "referenced_article_ids": [1],
             "context": "c", "implication": "i", "watch_points": "w"},
        ],
    }]}
    arts = {i: types.SimpleNamespace(source="한겨레") for i in (1, 2, 3)}
    m = ab_probe._metrics(analysis, arts)

    assert m["deep_issue_count"] == 2
    assert m["confidence"] == {"높음": 1, "중간": 0, "판단유보": 1}, m["confidence"]
    assert m["insight_with_number"] == 1      # "AI 18건" — 숫자 있음
    assert m["insight_with_limitation"] == 1
    # 참조 3건이 전부 한겨레 → 편중 1건. 참조 1건짜리는 중복 경고 안 함
    assert m["single_source"] == 1, m


if __name__ == "__main__":
    failures = 0
    tests = [
        test_agg_single_run_shows_raw_value,
        test_agg_identical_runs_omit_range,
        test_agg_spread_is_shown,
        test_agg_empty_is_safe,
        test_compare_outputs_both_labels_and_warning,
        test_compare_missing_label_fails_loudly,
        test_compare_insight_flag_prints_bodies,
        test_metrics_counts_from_analysis,
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
