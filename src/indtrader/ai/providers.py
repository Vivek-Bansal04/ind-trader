"""Provider adapters — thin HTTP, no SDKs, 30s timeout, None on any failure.
Keys come from env only. These cost nothing until you buy a key (the switch-point)."""
from __future__ import annotations

import httpx

from indtrader.ai.base import AIInsight
from indtrader.ai.prompts import SYSTEM
from indtrader.ai.router import parse_insight
from indtrader.config import Settings

TIMEOUT = 30.0


class _KeyProvider:
    name = "?"
    key: str = ""

    def available(self) -> bool:
        return bool(self.key)

    def analyze(self, prompt: str) -> AIInsight | None:
        try:
            text = self._chat(SYSTEM + "\n\n" + prompt)
            return parse_insight(self.name, text) if text else None
        except Exception:
            return None

    def _chat(self, full_prompt: str) -> str | None:  # pragma: no cover - network
        raise NotImplementedError


class OpenAIProvider(_KeyProvider):
    name = "openai"

    def __init__(self, s: Settings):
        self.key = s.openai_api_key
        self.model = s.openai_model

    def _chat(self, full_prompt: str) -> str | None:
        r = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.key}"},
            json={"model": self.model, "messages": [{"role": "user", "content": full_prompt}], "temperature": 0.2},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class GrokProvider(OpenAIProvider):
    name = "grok"

    def __init__(self, s: Settings):
        self.key = s.grok_api_key
        self.model = "grok-2-latest"

    def _chat(self, full_prompt: str) -> str | None:
        r = httpx.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.key}"},
            json={"model": self.model, "messages": [{"role": "user", "content": full_prompt}], "temperature": 0.2},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class AnthropicProvider(_KeyProvider):
    name = "anthropic"

    def __init__(self, s: Settings):
        self.key = s.anthropic_api_key
        self.model = s.anthropic_model

    def available(self) -> bool:
        return bool(self.key)

    def _chat(self, full_prompt: str) -> str | None:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.key, "anthropic-version": "2023-06-01"},
            json={"model": self.model, "max_tokens": 400, "messages": [{"role": "user", "content": full_prompt}]},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]


class GeminiProvider(_KeyProvider):
    name = "gemini"

    def __init__(self, s: Settings):
        self.key = s.gemini_api_key
        self.model = s.gemini_model

    def _chat(self, full_prompt: str) -> str | None:
        r = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.key},
            json={"contents": [{"parts": [{"text": full_prompt}]}],
                 "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]


class OllamaProvider(_KeyProvider):
    """Local model — free switch-point, no key needed."""

    name = "ollama"

    def __init__(self, s: Settings):
        self.key = ""  # availability is checked by probing the server below
        self.base = s.ollama_base_url
        self.model = s.ollama_model

    def available(self) -> bool:
        try:
            return httpx.get(f"{self.base}/api/tags", timeout=1.5).status_code == 200
        except Exception:
            return False

    def _chat(self, full_prompt: str) -> str | None:
        r = httpx.post(
            f"{self.base}/api/chat",
            json={"model": self.model, "messages": [{"role": "user", "content": full_prompt}], "stream": False,
                  "format": "json"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["message"]["content"]
