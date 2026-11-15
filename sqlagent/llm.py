"""LLM clients. Everything the agent needs from a model is `complete(messages)`."""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Callable, Protocol


def retry_after_seconds(message: str) -> float | None:
    """Parse Groq's 'Please try again in 1m5.2s' / '450ms' hint."""
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m(?!s))?(?:([\d.]+)s)?", message)
    if m and any(m.groups()):
        h, mnt, sec = (float(g) if g else 0.0 for g in m.groups())
        return h * 3600 + mnt * 60 + sec
    m = re.search(r"try again in ([\d.]+)ms", message)
    return float(m.group(1)) / 1000 if m else None


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMClient(Protocol):
    name: str

    def complete(self, messages: list[dict]) -> LLMResponse: ...


class GroqClient:
    """Groq-hosted models (same provider as v1). Retries on rate limits, which matter
    when running the benchmark on the free tier. Reasoning models (gpt-oss, qwen3) spend
    output tokens on thinking, so max_tokens is generous."""

    def __init__(self, model: str | None = None, temperature: float = 0.0, max_retries: int = 6,
                 max_tokens: int = 8192):
        from groq import Groq  # imported lazily so tests run without the SDK/key

        self.model = model or os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        self.name = f"groq/{self.model}"
        self.temperature = temperature
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self._client = Groq(api_key=os.environ["GROQ_API_KEY"])

    def complete(self, messages: list[dict]) -> LLMResponse:
        import groq

        delay = 2.0
        for attempt in range(self.max_retries):
            try:
                r = self._client.chat.completions.create(
                    model=self.model, messages=messages, temperature=self.temperature, max_tokens=self.max_tokens
                )
                u = r.usage
                return LLMResponse(
                    text=r.choices[0].message.content or "",
                    prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(u, "completion_tokens", 0) or 0,
                )
            except groq.RateLimitError as e:
                wait = retry_after_seconds(str(e))
                # Per-minute limits clear in seconds: wait and retry. The daily limit (or any
                # wait over 2 minutes) won't clear during a run, so give up immediately.
                if "per day" in str(e) or (wait or 0) > 120 or attempt == self.max_retries - 1:
                    raise
                time.sleep((wait or delay) + 0.5)
                delay = min(delay * 2, 60)
            except (groq.APIConnectionError, groq.InternalServerError):
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 60)
        raise RuntimeError("unreachable")


class ScriptedClient:
    """Offline client for tests and for validating the benchmark harness.

    `responder(messages, call_index) -> str` decides what the "model" says.
    """

    def __init__(self, responder: Callable[[list[dict], int], str], name: str = "scripted"):
        self.responder = responder
        self.name = name
        self.calls = 0

    def complete(self, messages: list[dict]) -> LLMResponse:
        text = self.responder(messages, self.calls)
        self.calls += 1
        prompt_chars = sum(len(m["content"]) for m in messages)
        return LLMResponse(text=text, prompt_tokens=prompt_chars // 4, completion_tokens=len(text) // 4)
