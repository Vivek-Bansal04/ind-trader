"""AI layer — the stock-analyzer pattern, improved: a provider-agnostic router so today
(zero keys) and a paid model later differ by ONE env line. The deterministic pipeline never
depends on this; every caller treats AI as an optional annotation of its own numbers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AIInsight:
    provider: str
    verdict: str  # BUY|HOLD|SELL|UNPARSEABLE
    confidence: int  # 0..100
    summary: str
    risks: str
    watch_for: str = ""  # "what would change the view" — falsifier from the model


class AIProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def analyze(self, prompt: str) -> AIInsight | None:
        """Return None on ANY failure — callers must handle absent AI gracefully."""
        ...
