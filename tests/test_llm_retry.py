"""LLM 재시도 정책 회귀 테스트.

배경 (2026-06-01 운영 장애): Gemini API가 503 UNAVAILABLE(일시적 과부하)을
반환하면서 분석 단계 전체가 실패하고 그날 브리핑이 빈 껍데기로 생성됐다.
원인: 코드 레벨 재시도가 없어 SDK 내부 재시도 소진 후 바로 치명 오류로 떨어짐.
(OpenAI 폴백 키도 미설정이라 폴백도 무력)

대응: GeminiProvider.generate가 503/500(ServerError, 5xx)에 한해 코드 레벨
재시도(3회, 지수 backoff). ClientError(4xx: 400/401/403/429)는 재시도 안 함
— 재시도해도 같은 실패이고, 특히 429 한도 초과를 재시도로 뚫으면 안 됨.

이 테스트는 재시도 분류 로직과 Retrying 조합 동작을 검증한다.
실제 SDK 클라이언트 없이 가짜 예외로 정책만 확인 (가볍고 결정론적).

실행: python tests/test_llm_retry.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential
from google.genai import errors

from llm import _is_retryable_gemini_error


def _server_error(code=503):
    return errors.ServerError(
        code, {"error": {"code": code, "message": "overload", "status": "UNAVAILABLE"}}
    )


def _client_error(code=400):
    return errors.ClientError(
        code, {"error": {"code": code, "message": "bad", "status": "INVALID_ARGUMENT"}}
    )


def _fast_retryer():
    """프로덕션과 동일 정책, backoff만 짧게 (테스트 속도)."""
    return Retrying(
        retry=retry_if_exception(_is_retryable_gemini_error),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.001, max=0.005),
        reraise=True,
    )


def test_classify_retryable():
    """5xx만 재시도 대상, 4xx와 일반 예외는 아님."""
    assert _is_retryable_gemini_error(_server_error(503)) is True
    assert _is_retryable_gemini_error(_server_error(500)) is True
    assert _is_retryable_gemini_error(_client_error(400)) is False
    assert _is_retryable_gemini_error(_client_error(401)) is False
    assert _is_retryable_gemini_error(_client_error(403)) is False
    # 429(한도 초과)는 재시도 안 함 — 재시도로 뚫으려 하면 안 되는 케이스
    assert _is_retryable_gemini_error(_client_error(429)) is False
    assert _is_retryable_gemini_error(ValueError("x")) is False


def test_503_retries_then_fails():
    """지속 503이면 3회 시도 후 최종 실패(reraise)."""
    calls = {"n": 0}

    def always_503():
        calls["n"] += 1
        raise _server_error(503)

    try:
        _fast_retryer()(always_503)
        assert False, "예외가 reraise 되어야 함"
    except errors.ServerError:
        pass
    assert calls["n"] == 3, f"503은 3회 시도해야 함, 실제 {calls['n']}회"


def test_400_no_retry():
    """4xx는 즉시 실패, 재시도 안 함."""
    calls = {"n": 0}

    def always_400():
        calls["n"] += 1
        raise _client_error(400)

    try:
        _fast_retryer()(always_400)
        assert False, "예외가 reraise 되어야 함"
    except errors.ClientError:
        pass
    assert calls["n"] == 1, f"4xx는 1회만 시도해야 함, 실제 {calls['n']}회"


def test_429_no_retry():
    """429 한도 초과는 재시도 안 함 (재시도로 뚫으면 안 됨)."""
    calls = {"n": 0}

    def always_429():
        calls["n"] += 1
        raise _client_error(429)

    try:
        _fast_retryer()(always_429)
        assert False
    except errors.ClientError:
        pass
    assert calls["n"] == 1, f"429는 1회만, 실제 {calls['n']}회"


def test_503_then_recover():
    """일시적 503 후 복구되면 성공 반환."""
    calls = {"n": 0}

    def fail_once_then_ok():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _server_error(503)
        return "성공"

    result = _fast_retryer()(fail_once_then_ok)
    assert result == "성공"
    assert calls["n"] == 2, f"1회 실패 후 2회차 성공해야 함, 실제 {calls['n']}회"


if __name__ == "__main__":
    failures = 0
    tests = [
        test_classify_retryable,
        test_503_retries_then_fails,
        test_400_no_retry,
        test_429_no_retry,
        test_503_then_recover,
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