"""
Advanced LLM wrapper with robust error handling and retry logic.

Supports OpenRouter API for accessing various LLM models.
"""

import os
import asyncio
import json
import time
from typing import List, Dict, Optional
from dataclasses import dataclass

try:
    from openai import AsyncOpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


@dataclass
class LLMResponse:
    """Response from LLM call.

    Besides the legacy aggregate ``tokens_used`` (prompt + completion),
    every response carries the per-call audit fields that the failure-mode
    analysis needs (design requirement: classify compute-bound failures
    from the *actual* per-request limit and finish_reason, never from
    global token thresholds):

    - finish_reason        provider stop reason ("stop", "length", ...)
    - prompt_tokens        input tokens as reported by the usage object
    - completion_tokens    output tokens (includes hidden reasoning where
                           the provider bills it as completion)
    - reasoning_tokens     hidden reasoning tokens if reported separately
    - requested_max_tokens the max_tokens limit sent with THIS request
    - retries              number of retried attempts before this response
    - retry_errors         '; '-joined error summaries of retried attempts
    - response_model       exact model identifier echoed by the API
    - provider             upstream provider (OpenRouter routing), if echoed
    - system_fingerprint   backend fingerprint, if echoed
    """
    success: bool
    content: str
    error: Optional[str] = None
    tokens_used: Optional[int] = None
    time_seconds: Optional[float] = None
    finish_reason: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    requested_max_tokens: Optional[int] = None
    requested_reasoning: Optional[str] = None  # verbatim reasoning request JSON
    retries: int = 0
    retry_errors: Optional[str] = None
    response_model: Optional[str] = None
    provider: Optional[str] = None
    system_fingerprint: Optional[str] = None

    def audit_fields(self) -> Dict[str, object]:
        """Per-call audit fields, keyed like the metrics CSV columns."""
        return {
            "finish_reason": self.finish_reason,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "requested_max_tokens": self.requested_max_tokens,
            "requested_reasoning": self.requested_reasoning,
            "retries": self.retries,
            "retry_errors": self.retry_errors,
            "response_model": self.response_model,
            "provider": self.provider,
            "system_fingerprint": self.system_fingerprint,
        }


class RobustLLM:
    """
    Robust LLM client with retry logic and error handling.

    Supports both Anthropic API and OpenRouter API.
    """

    # Default per-request timeout for cloud APIs (OpenRouter, Anthropic).
    # Local Ollama inference on large models (31B+) can take several
    # minutes per token-heavy prompt; the Ollama HTTP server itself has
    # a ~6-minute keep-alive that drops connections before the client
    # timeout fires. We therefore use a separate, larger default for
    # Ollama so that the client waits long enough.
    _DEFAULT_TIMEOUT_CLOUD: float = 120.0
    _DEFAULT_TIMEOUT_OLLAMA: float = 600.0

    def __init__(
        self,
        model: str = "openai/gpt-5.5",
        # None = OMIT the parameter entirely. GPT-5.x endpoints do not
        # support sampling parameters (temperature/top_p); with
        # require_parameters=true a request that carries temperature
        # matches NO endpoint (OpenRouter routing 404).
        temperature: Optional[float] = None,
        # 32k default ceiling: always-on-thinking models can burn >16k
        # tokens on hidden reasoning for hard instances even at low effort
        # (finish_reason=length with empty content). Models that stop
        # early are unaffected by a higher ceiling.
        max_tokens: int = 32000,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        timeout: float = 0.0,   # 0 = use provider-specific default
        enable_reasoning: bool = False,
        reasoning_max_tokens: int = 2000,
        reasoning_effort: str = None,  # e.g. 'none'/'minimal'/'medium': effort-level control
        use_ollama: bool = False,
        ollama_base_url: str = "http://ollama:11434",
        require_parameters: bool = False,
        allow_fallbacks: bool = True,
        provider_order: list = None,
    ):
        """
        Initialize the LLM client.

        Args:
            model: Model identifier ("vendor/model" for OpenRouter, a bare
                   id for direct Anthropic, or an Ollama model name)
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            max_retries: Number of retry attempts on failure
            retry_delay: Delay between retries in seconds
            timeout: Per-request timeout in seconds. ``0`` (the default)
                selects the provider-specific default: 600 s for Ollama
                (local inference on large models can be slow), 120 s for
                cloud providers (OpenRouter, Anthropic direct). Pass an
                explicit positive value to override.
            enable_reasoning: Enable the reasoning mode (explicit thinking
                budget via the OpenRouter unified reasoning parameter)
            reasoning_max_tokens: Max tokens for reasoning (default: 2000)
            use_ollama: Use Ollama API instead of OpenRouter/Anthropic
            ollama_base_url: Base URL for Ollama API (default: http://ollama:11434)
            require_parameters: OpenRouter only — refuse providers that do
                not support every requested parameter (prevents silent
                dropping of the reasoning configuration)
            allow_fallbacks: OpenRouter only — set False to disable
                cross-provider fallback routing
            provider_order: OpenRouter only — explicit upstream provider
                preference list (pins routing)
        """
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        if timeout <= 0:
            timeout = self._DEFAULT_TIMEOUT_OLLAMA if use_ollama else self._DEFAULT_TIMEOUT_CLOUD
        self.timeout = timeout
        self.enable_reasoning = enable_reasoning
        self.reasoning_max_tokens = reasoning_max_tokens
        self.reasoning_effort = reasoning_effort
        self.use_ollama = use_ollama
        self.ollama_base_url = ollama_base_url
        self.require_parameters = require_parameters
        self.allow_fallbacks = allow_fallbacks
        self.provider_order = provider_order

        # Determine which API to use
        if self.use_ollama:
            # Use Ollama (OpenAI-compatible API)
            if not HAS_OPENAI:
                raise ImportError("openai package required for Ollama. Install with: pip install openai")

            self.client = AsyncOpenAI(
                api_key="ollama",  # Ollama doesn't require a real API key
                base_url=f"{ollama_base_url}/v1"
            )
            self.use_openrouter = False

        elif "/" in model:
            # Use OpenRouter
            self.use_openrouter = True

            if not HAS_OPENAI:
                raise ImportError("openai package required for OpenRouter. Install with: pip install openai")

            api_key = os.getenv("OPENROUTER_API_KEY")
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY environment variable not set")

            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1"
            )
        else:
            # Use Anthropic directly
            self.use_openrouter = False

            if not HAS_ANTHROPIC:
                raise ImportError("anthropic package required for Anthropic API. Install with: pip install anthropic")

            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable not set")

            self.client = anthropic.AsyncAnthropic(api_key=api_key)

    async def call(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None
    ) -> LLMResponse:
        """
        Call the LLM with messages.

        Args:
            messages: List of message dicts with "role" and "content"
            system: Optional system prompt

        Returns:
            LLMResponse object
        """
        start_time = time.time()

        # Retry audit trail: the number and kind of retries per trial
        # are recorded in the audit log (the single-shot property is
        # "one *completed* model call", transport retries included).
        retry_errors: List[str] = []

        for attempt in range(self.max_retries):
            try:
                if self.use_openrouter or self.use_ollama:
                    # OpenRouter and Ollama use OpenAI-compatible API
                    openai_messages = []

                    # Add system message if provided
                    if system:
                        openai_messages.append({
                            "role": "system",
                            "content": system
                        })

                    # Add other messages
                    for msg in messages:
                        role = msg["role"]
                        content = msg["content"]

                        # Handle system messages in message list
                        if role == "system":
                            openai_messages.append({
                                "role": "system",
                                "content": content
                            })
                        else:
                            openai_messages.append({
                                "role": role,
                                "content": content
                            })

                    # Make the API call
                    call_kwargs = {
                        "model": self.model,
                        "messages": openai_messages,
                        "max_tokens": self.max_tokens,
                        "timeout": self.timeout
                    }
                    # temperature is OPTIONAL: None means the parameter
                    # is not sent at all (the GPT-5.x endpoints declare
                    # no sampling-parameter support; sending one under
                    # require_parameters excludes every endpoint).
                    if self.temperature is not None:
                        call_kwargs["temperature"] = self.temperature

                    # Reasoning configuration: build the exact request body
                    # once and log it verbatim per trial (audit requirement:
                    # the model_tag alone is not evidence of what was sent).
                    extra_body = {}
                    if self.enable_reasoning:
                        extra_body["reasoning"] = {
                            "max_tokens": self.reasoning_max_tokens}
                    elif self.reasoning_effort and self.use_openrouter:
                        # Always-on reasoning models reason by default and
                        # can spend the entire max_tokens budget on hidden
                        # reasoning (empty content, finish_reason=length).
                        # Control via effort level.
                        extra_body["reasoning"] = {
                            "effort": self.reasoning_effort}
                    if self.use_openrouter and (
                            self.require_parameters
                            or not self.allow_fallbacks
                            or self.provider_order):
                        prov = {}
                        if self.require_parameters:
                            prov["require_parameters"] = True
                        if not self.allow_fallbacks:
                            prov["allow_fallbacks"] = False
                        if self.provider_order:
                            prov["order"] = list(self.provider_order)
                        extra_body["provider"] = prov
                    if extra_body:
                        call_kwargs["extra_body"] = extra_body
                    requested_reasoning = (
                        json.dumps(extra_body["reasoning"])
                        if extra_body.get("reasoning") else None)

                    response = await self.client.chat.completions.create(**call_kwargs)

                    # Extract content
                    message = response.choices[0].message
                    content = message.content or ""
                    finish_reason = response.choices[0].finish_reason

                    elapsed_time = time.time() - start_time

                    # Token accounting: separate prompt / completion /
                    # reasoning counts (never only the sum — the sum cannot
                    # be compared against max_tokens, which limits
                    # completion only).
                    tokens_used = None
                    prompt_tokens = completion_tokens = reasoning_tokens = None
                    if response.usage:
                        prompt_tokens = response.usage.prompt_tokens
                        completion_tokens = response.usage.completion_tokens
                        tokens_used = (prompt_tokens or 0) + (completion_tokens or 0)
                        details = getattr(
                            response.usage, "completion_tokens_details", None)
                        if details is not None:
                            reasoning_tokens = getattr(
                                details, "reasoning_tokens", None)

                    audit = dict(
                        finish_reason=finish_reason,
                        requested_reasoning=requested_reasoning,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        reasoning_tokens=reasoning_tokens,
                        requested_max_tokens=self.max_tokens,
                        retries=attempt,
                        retry_errors="; ".join(retry_errors) or None,
                        response_model=getattr(response, "model", None),
                        # OpenRouter echoes the upstream provider as an
                        # extra field; absent on plain OpenAI/Ollama.
                        provider=getattr(response, "provider", None),
                        system_fingerprint=getattr(
                            response, "system_fingerprint", None),
                    )

                    # Empty content is a harness-visible failure, not a model
                    # answer: typically the model spent its whole max_tokens
                    # budget on hidden reasoning (finish_reason=length).
                    # Report it distinctly so it is not misclassified as a
                    # parse failure downstream.
                    if not content.strip():
                        reasoning_present = bool(getattr(message, "reasoning", None))
                        return LLMResponse(
                            success=False,
                            content="",
                            error=(f"Empty model content "
                                   f"(finish_reason={finish_reason}, "
                                   f"reasoning_field={'present' if reasoning_present else 'absent'}, "
                                   f"tokens_used={tokens_used})"),
                            tokens_used=tokens_used,
                            time_seconds=elapsed_time,
                            **audit,
                        )

                    return LLMResponse(
                        success=True,
                        content=content,
                        error=None,
                        tokens_used=tokens_used,
                        time_seconds=elapsed_time,
                        **audit,
                    )

                else:
                    # Direct Anthropic API
                    anthropic_messages = []
                    for msg in messages:
                        role = msg["role"]
                        content = msg["content"]

                        # Anthropic only accepts "user" and "assistant" roles
                        if role == "system":
                            # Move system messages to the system parameter
                            if system is None:
                                system = content
                            else:
                                system += "\n\n" + content
                            continue

                        anthropic_messages.append({
                            "role": role,
                            "content": content
                        })

                    # Build request parameters
                    params = {
                        "model": self.model,
                        "messages": anthropic_messages,
                        "max_tokens": self.max_tokens,
                        "temperature": self.temperature,
                        "timeout": self.timeout
                    }

                    if system:
                        params["system"] = system

                    # Make the API call
                    response = await self.client.messages.create(**params)

                    # Extract content
                    content = ""
                    if response.content:
                        for block in response.content:
                            if hasattr(block, "text"):
                                content += block.text

                    elapsed_time = time.time() - start_time

                    # Get token usage if available
                    tokens_used = None
                    prompt_tokens = completion_tokens = None
                    if hasattr(response, "usage"):
                        prompt_tokens = response.usage.input_tokens
                        completion_tokens = response.usage.output_tokens
                        tokens_used = (prompt_tokens or 0) + (completion_tokens or 0)

                    return LLMResponse(
                        success=True,
                        content=content,
                        error=None,
                        tokens_used=tokens_used,
                        time_seconds=elapsed_time,
                        finish_reason=getattr(response, "stop_reason", None),
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        requested_max_tokens=self.max_tokens,
                        retries=attempt,
                        retry_errors="; ".join(retry_errors) or None,
                        response_model=getattr(response, "model", None),
                    )

            except Exception as e:
                error_msg = f"API error: {type(e).__name__}: {str(e)}"

                # Check if we should retry
                should_retry = False
                if attempt < self.max_retries - 1:
                    error_str = str(e).lower()
                    if any(keyword in error_str for keyword in ["timeout", "rate_limit", "overloaded", "503", "502", "500"]):
                        should_retry = True
                        retry_errors.append(
                            f"attempt {attempt + 1}: {type(e).__name__}")
                        delay = self.retry_delay * (attempt + 1)
                        if "rate_limit" in error_str:
                            delay *= 2
                        await asyncio.sleep(delay)

                if should_retry:
                    continue
                else:
                    elapsed_time = time.time() - start_time
                    return LLMResponse(
                        success=False,
                        content="",
                        error=error_msg,
                        time_seconds=elapsed_time,
                        requested_max_tokens=self.max_tokens,
                        retries=attempt,
                        retry_errors="; ".join(retry_errors) or None,
                    )

        # Should never reach here
        elapsed_time = time.time() - start_time
        return LLMResponse(
            success=False,
            content="",
            error="Max retries exceeded",
            time_seconds=elapsed_time,
            requested_max_tokens=self.max_tokens,
            retries=self.max_retries,
            retry_errors="; ".join(retry_errors) or None,
        )


# Synchronous wrapper for backwards compatibility
class RobustLLMSync:
    """Synchronous wrapper for RobustLLM."""

    def __init__(self, **kwargs):
        self.llm = RobustLLM(**kwargs)

    def call(self, messages: List[Dict[str, str]], system: Optional[str] = None) -> LLMResponse:
        """Synchronous call wrapper."""
        return asyncio.run(self.llm.call(messages, system))
