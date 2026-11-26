"""LLM 추상화 레이어.

폴백 체인: Gemini Flash 최신 → Gemini Flash-Lite 최신 → Gemini 3.5 Flash-Lite
→ GPT-5 mini (OPENAI_API_KEY가 있을 때만)."""

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


# 1차는 버전을 고정하지 않고 최신 Flash 별칭을 쓴다. 구버전 고정(2.5)이
# 단종 위험을 안고 있어서다. 대신 별칭 뒤 실제 버전이 바뀌면 분석 품질도
# 예고 없이 바뀔 수 있으므로, 응답의 model_version을 매 호출 로그에 남긴다.
GEMINI_MODEL = "gemini-flash-latest"
# 2차 폴백: 같은 무료 키, Flash와 용량 풀이 달라 Flash가 503(high demand)일
# 때도 대개 응답한다. 품질은 Flash보다 낮지만 빈 브리핑보다 낫다.
GEMINI_FALLBACK_MODEL = "gemini-flash-lite-latest"
# 3차 폴백: 버전 고정. 무료 티어 사용량 한도가 가장 넉넉한 모델이라, 별칭
# 둘이 한도·과부하로 막혀도 마지막까지 버틴다.
GEMINI_LAST_RESORT_MODEL = "gemini-3.5-flash-lite"
GEMINI_API_VERSION = "v1beta"
OPENAI_MODEL = "gpt-5-mini"

_log = logging.getLogger("llm.retry")


# 재시도 정책 (Gemini 일시적 과부하 대응)
# - ServerError(5xx: 503 UNAVAILABLE, 500 INTERNAL)만 재시도 — 일시적 과부하라 풀림
# - ClientError(4xx: 400/401/403/429)는 재시도 안 함 — 재시도해도 같은 실패
#   (특히 429 한도 초과는 재시도로 뚫으려 하면 안 됨)
# - 4회, 지수 backoff 4→8→16초 대기 (총 최대 ~28초, GitHub Actions 타임아웃 여유 안)
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
    name = GEMINI_MODEL

    def __init__(self, api_key: str, model: str = GEMINI_MODEL, attempts: int = 4):
        # google-genai (신 SDK) 사용
        from google import genai
        from google.genai import types
        # api_version 고정: google-genai 3.x부터 기본값이 v1로 바뀌었는데 v1은
        # system_instruction을 거부한다("Developer instruction is not enabled for
        # api version v1", 400). requirements가 하한만 걸려 있어 CI가 새 SDK를 받는
        # 순간 전 호출이 실패했다(2026-11-25). v1beta는 구·신 SDK 모두 지원.
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(api_version=GEMINI_API_VERSION),
        )
        self._genai = genai
        # 모델을 인스턴스 단위로 고정한다. 기본값은 운영 모델(GEMINI_MODEL)이라
        # 기존 호출부는 그대로 동작하고, ab_probe가 다른 모델을 지정해 같은 입력으로
        # 비교할 수 있다. name도 인스턴스 속성으로 덮어써 로그·Notion '모델' 속성에
        # 실제 사용 모델이 남는다.
        self._model = model
        self.name = model
        # 5xx 재시도 횟수. 폴백 단계는 줄여서, 전 모델 장애 시 체인 전체 대기가
        # 워크플로 타임아웃(25분)을 넘지 않게 한다.
        self._attempts = attempts
        # 마지막 호출에서 응답이 알려준 실제 모델 버전. 별칭(…-latest)을 쓰면
        # name과 다르므로 Notion '모델' 속성에는 이 값을 기록한다.
        self.last_model: Optional[str] = None

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
                model=self._model,
                contents=user,
                config=config,
            )

        retryer = Retrying(
            retry=retry_if_exception(_is_retryable_gemini_error),
            stop=stop_after_attempt(self._attempts),
            wait=wait_exponential(multiplier=4, max=16),
            reraise=True,
            before_sleep=lambda rs: print(
                f"    [재시도] Gemini 5xx, {rs.attempt_number}회 시도 실패 후 대기"
            ),
        )
        resp = retryer(_call)
        self.last_model = getattr(resp, "model_version", None) or self._model

        # 진단: finish_reason과 사용 토큰 출력
        try:
            candidates = getattr(resp, "candidates", None) or []
            if candidates:
                fr = getattr(candidates[0], "finish_reason", None)
                if fr is not None:
                    print(f"    [Gemini] finish_reason={fr}")
            print(f"    [Gemini] model_version={self.last_model}")
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
        self.last_model: Optional[str] = None

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
        self.last_model = getattr(resp, "model", None) or OPENAI_MODEL
        text = resp.choices[0].message.content
        if not text:
            raise RuntimeError("OpenAI returned empty response")
        return text


# ----------------------------------------------------------------------
# 폴백 래퍼
# ----------------------------------------------------------------------
def _served_model(provider: LLMProvider) -> str:
    """provider가 실제로 응답한 모델명. 모르면 설정 모델명(name)."""
    return getattr(provider, "last_model", None) or provider.name


class LLMWithFallback:
    """1차 LLM 실패 시 폴백 체인을 순서대로 시도. None인 폴백은 건너뛴다."""

    def __init__(self, primary: LLMProvider, *fallbacks: Optional[LLMProvider]):
        self.primary = primary
        self.fallbacks = [f for f in fallbacks if f is not None]
        self.fallback = self.fallbacks[0] if self.fallbacks else None
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
        chain = [self.primary, *self.fallbacks]
        for i, provider in enumerate(chain):
            if i > 0:
                print(f"  [폴백] {provider.name}로 재시도")
            try:
                result = provider.generate(system, user, max_tokens, json_mode, thinking_budget, temperature)
            except Exception as e:
                print(f"  [경고] {provider.name} 실패: {e}")
                if i == len(chain) - 1:
                    raise
                continue
            self.last_used = _served_model(provider)
            return result
        raise AssertionError("unreachable")


# ----------------------------------------------------------------------
# 팩토리
# ----------------------------------------------------------------------
def get_llm() -> LLMWithFallback:
    """환경변수에서 키를 읽어 LLM 인스턴스 구성.
    
    GEMINI_API_KEY: 필수 (1~3차)
    OPENAI_API_KEY: 선택 (마지막 폴백). 없으면 Gemini 체인만 사용.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if not gemini_key:
        # Gemini 키 없으면 OpenAI 단독으로라도 동작
        if openai_key:
            print("[LLM] GEMINI_API_KEY 없음. OpenAI 단독 모드.")
            return LLMWithFallback(OpenAIProvider(openai_key))
        raise RuntimeError("GEMINI_API_KEY 또는 OPENAI_API_KEY 중 하나는 필수입니다")

    # 폴백 없이 돌면 Flash 503 한 번에 그날 분석이 통째로 빠진다
    # (2026-06-01, 06-11, 09-30 실측). 같은 키로 다른 모델에 먼저 폴백하고,
    # 유료인 OpenAI는 Gemini가 전부 실패했을 때만 쓴다.
    llm = LLMWithFallback(
        GeminiProvider(gemini_key),
        GeminiProvider(gemini_key, model=GEMINI_FALLBACK_MODEL, attempts=2),
        GeminiProvider(gemini_key, model=GEMINI_LAST_RESORT_MODEL, attempts=2),
        OpenAIProvider(openai_key) if openai_key else None,
    )
    names = " → ".join(p.name for p in [llm.primary, *llm.fallbacks])
    print(f"[LLM] 체인: {names}")
    return llm