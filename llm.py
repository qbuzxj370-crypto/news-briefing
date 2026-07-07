"""LLM 추상화 레이어: Gemini 2.5 Flash (1차) + GPT-5 mini (폴백)."""

from __future__ import annotations

import os
import logging
from typing import Optional, Protocol

from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)


GEMINI_MODEL = "gemini-2.5-flash"
OPENAI_MODEL = "gpt-5-mini"

_log = logging.getLogger("llm.retry")


# 재시도 정책 (Gemini 일시적 과부하 대응)
# - ServerError(5xx: 503 UNAVAILABLE, 500 INTERNAL)만 재시도 — 일시적 과부하라 풀림
# - ClientError(4xx: 400/401/403/429)는 재시도 안 함 — 재시도해도 같은 실패
#   (특히 429 한도 초과는 재시도로 뚫으려 하면 안 됨)
# - 3회, 지수 backoff 4→8→16초 (총 최대 ~28초, GitHub Actions 타임아웃 여유 안)
def _is_retryable_gemini_error(exc: BaseException) -> bool:
    """ServerError(5xx)만 재시도 대상. 지연 import로 SDK 의존."""
    try:
        from google.genai import errors
        return isinstance(exc, errors.ServerError)
    except Exception:
        return False


# ----------------------------------------------------------------------
# 인터페이스
# ----------------------------------------------------------------------
class LLMProvider(Protocol):
    name: str

    def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 8000,
        json_mode: bool = False,
        thinking_budget: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        ...


# ----------------------------------------------------------------------
# Gemini Provider
# ----------------------------------------------------------------------
class GeminiProvider:
    name = "gemini-2.5-flash"

    def __init__(self, api_key: str):
        # google-genai (신 SDK) 사용
        from google import genai
        self._client = genai.Client(api_key=api_key)
        self._genai = genai

    def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 8000,
        json_mode: bool = False,
        thinking_budget: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        from google.genai import types
        # temperature=None이면 기본 0.5(분석 등 창의성 필요한 호출). 분류는 0을
        # 명시해 라벨 결정성을 높인다 (ADR-017 0c).
        config_kwargs = {
            "system_instruction": system,
            "max_output_tokens": max_tokens,
            "temperature": 0.5 if temperature is None else temperature,
        }
        # thinking_budget: None=기본(thinking 활성화, Gemini가 알아서 판단)
        #                  0=비활성화 (분류/구조화 출력 등 추론 불필요한 작업)
        #                  N>0=명시적 budget
        if thinking_budget is not None:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"
        config = types.GenerateContentConfig(**config_kwargs)

        # 503/500 등 일시적 서버 과부하에 코드 레벨 재시도.
        # SDK 내부 재시도와 별개로, 우리가 backoff를 제어해 폴백 전에 먼저 시도.
        def _call():
            return self._client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user,
                config=config,
            )

        retryer = Retrying(
            retry=retry_if_exception(_is_retryable_gemini_error),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=4, max=16),
            reraise=True,
            before_sleep=lambda rs: print(
                f"    [재시도] Gemini 5xx, {rs.attempt_number}회 시도 실패 후 대기"
            ),
        )
        resp = retryer(_call)

        # 진단: finish_reason과 사용 토큰 출력
        try:
            candidates = getattr(resp, "candidates", None) or []
            if candidates:
                fr = getattr(candidates[0], "finish_reason", None)
                if fr is not None:
                    print(f"    [Gemini] finish_reason={fr}")
            usage = getattr(resp, "usage_metadata", None)
            if usage:
                in_tok = getattr(usage, "prompt_token_count", None)
                out_tok = getattr(usage, "candidates_token_count", None)
                total = getattr(usage, "total_token_count", None)
                print(f"    [Gemini] tokens: input={in_tok}, output={out_tok}, total={total}")
        except Exception:
            pass

        text = resp.text
        if not text:
            raise RuntimeError("Gemini returned empty response")
        return text


# ----------------------------------------------------------------------
# OpenAI Provider (GPT-5 mini)
# ----------------------------------------------------------------------
class OpenAIProvider:
    name = "gpt-5-mini"

    def __init__(self, api_key: str):
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key)

    def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 8000,
        json_mode: bool = False,
        thinking_budget: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        # thinking_budget은 OpenAI에 무효 (Gemini 전용 파라미터). 시그니처 통일용.
        # temperature도 전달하지 않는다 — GPT-5 계열(추론 모델)은 기본값(1) 외
        # temperature를 거부할 수 있어, 폴백이 깨지지 않도록 기본 동작을 유지한다.
        # 분류 결정성은 1차(Gemini) temp=0로 확보하므로 드문 폴백은 기본으로 충분.
        # GPT-5 계열은 Responses API 권장이나 Chat Completions도 호환 유지됨.
        kwargs = {
            "model": OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_completion_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content
        if not text:
            raise RuntimeError("OpenAI returned empty response")
        return text


# ----------------------------------------------------------------------
# 폴백 래퍼
# ----------------------------------------------------------------------
class LLMWithFallback:
    """1차 LLM 실패 시 폴백으로 자동 전환."""

    def __init__(self, primary: LLMProvider, fallback: Optional[LLMProvider]):
        self.primary = primary
        self.fallback = fallback
        self.last_used: str = primary.name

    def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 8000,
        json_mode: bool = False,
        thinking_budget: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        try:
            result = self.primary.generate(system, user, max_tokens, json_mode, thinking_budget, temperature)
            self.last_used = self.primary.name
            return result
        except Exception as e:
            print(f"  [경고] {self.primary.name} 실패: {e}")
            if not self.fallback:
                raise
            print(f"  [폴백] {self.fallback.name}로 재시도")
            result = self.fallback.generate(system, user, max_tokens, json_mode, thinking_budget, temperature)
            self.last_used = self.fallback.name
            return result


# ----------------------------------------------------------------------
# 팩토리
# ----------------------------------------------------------------------
def get_llm() -> LLMWithFallback:
    """환경변수에서 키를 읽어 LLM 인스턴스 구성.
    
    GEMINI_API_KEY: 필수 (1차)
    OPENAI_API_KEY: 선택 (폴백). 없으면 폴백 없이 단일 모드.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if not gemini_key:
        # Gemini 키 없으면 OpenAI 단독으로라도 동작
        if openai_key:
            print("[LLM] GEMINI_API_KEY 없음. OpenAI 단독 모드.")
            return LLMWithFallback(OpenAIProvider(openai_key), None)
        raise RuntimeError("GEMINI_API_KEY 또는 OPENAI_API_KEY 중 하나는 필수입니다")

    primary = GeminiProvider(gemini_key)
    fallback = OpenAIProvider(openai_key) if openai_key else None

    if fallback:
        print(f"[LLM] 1차: {primary.name}, 폴백: {fallback.name}")
    else:
        print(f"[LLM] 1차: {primary.name} (폴백 없음)")

    return LLMWithFallback(primary, fallback)