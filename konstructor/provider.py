from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Protocol

@dataclass(frozen=True)
class ProviderCapabilities:
    structured_output: bool = True
    streaming: bool = False
    tools: bool = False

class LLMProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...
    def health_check(self) -> dict[str, Any]: ...
    def complete_structured(self, request: str, schema: dict[str, Any]) -> dict[str, Any]: ...

class DeterministicProvider:
    """Fallback local; não envia o workspace nem depende de SDK de IA."""
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()
    def health_check(self) -> dict[str, Any]:
        return {"status": "passed", "provider": "deterministic-local"}
    def complete_structured(self, request: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {"request": request, "schema": schema, "mode": "deterministic"}
