"""Router + JSON parsing. ai_provider_order decides everything; unknown names are skipped.
Adding a new vendor = one class + one line in build_router()."""
from __future__ import annotations

import json
import re

from indtrader.ai.base import AIInsight, AIProvider
from indtrader.config import Settings


def parse_insight(provider: str, text: str) -> AIInsight:
    """Extract the first {...} JSON object; degrade loudly on anything else."""
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        obj = json.loads(m.group(0)) if m else {}
        verdict = str(obj.get("verdict", "UNPARSEABLE")).upper()
        if verdict not in {"BUY", "HOLD", "SELL"}:
            verdict = "UNPARSEABLE"
        conf = int(obj.get("confidence", 0))
        return AIInsight(
            provider=provider,
            verdict=verdict,
            confidence=max(0, min(100, conf)),
            summary=str(obj.get("summary", ""))[:600],
            risks=str(obj.get("risks", ""))[:400],
            watch_for=str(obj.get("what_would_change_the_view", ""))[:300],
        )
    except Exception:
        return AIInsight(provider=provider, verdict="UNPARSEABLE", confidence=0,
                         summary=text[:200].replace("\n", " "), risks="model output unparsable")


class NullProvider:
    """Always 'available'; returns None so callers fall back to deterministic output."""

    name = "none"

    def available(self) -> bool:
        return True

    def analyze(self, prompt: str) -> AIInsight | None:
        return None


class Router:
    def __init__(self, providers: list[AIProvider]):
        self.providers = providers

    def first_available(self) -> AIProvider | None:
        for p in self.providers:
            if p.available():
                return p
        return None

    def analyze(self, prompt: str) -> AIInsight | None:
        """Try providers in order until one yields an insight; never raise."""
        for p in self.providers:
            try:
                if p.available():
                    out = p.analyze(prompt)
                    if out is not None:
                        return out
            except Exception as e:  # graceful by design — AI is an annotation, not a dependency
                print(f"[ai] provider {p.name} failed: {e}")
        return None


def build_router(settings: Settings) -> Router:
    from indtrader.ai.providers import AnthropicProvider, GeminiProvider, GrokProvider, OllamaProvider, OpenAIProvider

    registry = {
        "openai": OpenAIProvider(settings),
        "anthropic": AnthropicProvider(settings),
        "gemini": GeminiProvider(settings),
        "grok": GrokProvider(settings),
        "ollama": OllamaProvider(settings),
        "none": NullProvider(),
    }
    chain = [registry[name] for name in settings.ai_order if name in registry]
    if not any(p.name == "none" for p in chain):
        chain.append(NullProvider())
    return Router(chain)
