"""분류 temperature=0 배선 테스트 (ADR-017, Phase 0c).

배경: 분류는 라벨링 작업이라 결정성이 바람직하다. generate()에 temperature
파라미터를 신설해, 분류 호출만 temp=0을 쓰고 분석(기본 0.5)·OpenAI 폴백은
영향받지 않게 한다. 실제 LLM 라벨 안정성은 운영에서 관찰하고, 여기서는
'분류 경로가 temp 0을 전달하는지'와 'fallback 래퍼가 temperature를 forward
하는지'(배선)를 검증한다.

실행: python tests/test_classify_temperature.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import Article
from classifier import _classify_batch
from llm import LLMWithFallback

KST = timezone(timedelta(hours=9))


class RecordingProvider:
    """generate가 받은 인자를 기록하는 가짜 provider."""

    def __init__(self, name="rec"):
        self.name = name
        self.kwargs = None

    def generate(self, system, user, max_tokens=8000, json_mode=False,
                 thinking_budget=None, temperature=None):
        self.kwargs = {
            "max_tokens": max_tokens, "json_mode": json_mode,
            "thinking_budget": thinking_budget, "temperature": temperature,
        }
        return '{"items": []}'


class CaptureLLM:
    """_classify_batch가 generate에 넘기는 temperature를 포착."""

    last_used = "cap"

    def __init__(self):
        self.temperature = "UNSET"

    def generate(self, system, user, max_tokens=8000, json_mode=False,
                 thinking_budget=None, temperature=None):
        self.temperature = temperature
        return '{"items": [{"id": 1, "cat": "IT", "cross": false}]}'


def test_classify_batch_passes_temp_zero():
    """분류 배치 호출은 temperature=0을 명시한다."""
    llm = CaptureLLM()
    arts = [Article(title="제목", summary="", link="https://x/1",
                    published=datetime(2026, 6, 28, 8, tzinfo=KST), source="동아일보")]
    _classify_batch(llm, arts, offset=0)
    assert llm.temperature == 0, llm.temperature


def test_fallback_forwards_temperature():
    """LLMWithFallback이 temperature를 1차 provider로 forward한다."""
    p = RecordingProvider()
    llm = LLMWithFallback(p, None)
    llm.generate("sys", "usr", temperature=0)
    assert p.kwargs["temperature"] == 0, p.kwargs


def test_default_temperature_is_none():
    """temperature 미지정(분석 등)은 None으로 전달 → provider 기본값(Gemini 0.5)."""
    p = RecordingProvider()
    llm = LLMWithFallback(p, None)
    llm.generate("sys", "usr")
    assert p.kwargs["temperature"] is None, p.kwargs


def test_fallback_provider_also_receives_temperature():
    """1차 실패 시 폴백 provider에도 temperature가 전달된다 (배선 일관성)."""
    class Failing:
        name = "primary"
        def generate(self, *a, **k):
            raise RuntimeError("primary down")
    fb = RecordingProvider(name="fallback")
    llm = LLMWithFallback(Failing(), fb)
    llm.generate("sys", "usr", temperature=0)
    assert fb.kwargs["temperature"] == 0, fb.kwargs


if __name__ == "__main__":
    failures = 0
    tests = [
        test_classify_batch_passes_temp_zero,
        test_fallback_forwards_temperature,
        test_default_temperature_is_none,
        test_fallback_provider_also_receives_temperature,
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
