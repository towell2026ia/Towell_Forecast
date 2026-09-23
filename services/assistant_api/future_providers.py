"""Disabled future-provider contracts. No API client or network connection exists."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .orchestrator import AssistantProvider
from .runner import ResearchProvider


class OpenAIAssistantProvider(AssistantProvider):
    def render(self, intent: str | None, data: dict[str, Any]) -> str:
        raise RuntimeError("provider_disabled")

    def respond(self, message: str, context: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("provider_disabled")

    def health(self) -> dict[str, str]:
        return {"status": "disabled", "provider": "openai"}


class OpenAIDeepResearchProvider(ResearchProvider):
    def run(self, cutoff_date: str, chain: str, category: str | None = None,
            product: str | None = None,
            historical_context: dict[str, Any] | None = None) -> dict[str, Any]:
        raise RuntimeError("provider_disabled")

    def health(self) -> dict[str, str]:
        return {"status": "disabled", "provider": "openai_deep_research"}


class VoiceProvider(ABC):
    @abstractmethod
    def transcribe(self, audio: bytes) -> str: ...

    @abstractmethod
    def synthesize(self, text: str) -> bytes: ...


class DisabledVoiceProvider(VoiceProvider):
    def transcribe(self, audio: bytes) -> str:
        raise RuntimeError("provider_disabled")

    def synthesize(self, text: str) -> bytes:
        raise RuntimeError("provider_disabled")
