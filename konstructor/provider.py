from __future__ import annotations
from dataclasses import dataclass
import json
import os
import urllib.parse
import urllib.error
import urllib.request
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


class OpenAICompatibleProvider:
    """OpenAI chat-completions protocol over the standard library."""
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None, timeout: float = 60):
        self.base_url = (base_url or os.environ.get("KONSTRUCTOR_LLM_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.model = model or os.environ.get("KONSTRUCTOR_LLM_MODEL", "")
        self.api_key = api_key if api_key is not None else os.environ.get("KONSTRUCTOR_LLM_API_KEY", "")
        self.timeout = timeout
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("KONSTRUCTOR_LLM_BASE_URL must be an http(s) URL")

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self.model:
            raise ValueError("configure KONSTRUCTOR_LLM_MODEL")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.base_url + "/chat/completions", json.dumps(body).encode(), headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read(2_000_001))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc
        try:
            value = json.loads(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("LLM returned invalid structured JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError("LLM structured response must be a JSON object")
        return value

    def health_check(self) -> dict[str, Any]:
        return {"status": "ready" if self.model else "blocked", "provider": "openai-compatible", "model": self.model or None}

    def complete_structured(self, request: str, schema: dict[str, Any]) -> dict[str, Any]:
        value = self._post({"model": self.model, "messages": [{"role": "user", "content": request}],
                            "response_format": {"type": "json_schema", "json_schema": {"name": "konstructor_output", "strict": True, "schema": schema}}})
        _validate_schema(value, schema)
        return value


def _validate_schema(value: Any, schema: dict[str, Any]) -> None:
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict): raise RuntimeError("LLM output does not match object schema")
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing: raise RuntimeError(f"LLM output missing required fields: {', '.join(missing)}")
        props = schema.get("properties", {})
        for key, child in props.items():
            if key in value: _validate_schema(value[key], child)
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise RuntimeError("LLM output contains unexpected fields")
    elif kind == "array":
        if not isinstance(value, list): raise RuntimeError("LLM output does not match array schema")
        for item in value: _validate_schema(item, schema.get("items", {}))
    elif kind == "string" and not isinstance(value, str): raise RuntimeError("LLM output does not match string schema")
    elif kind == "boolean" and not isinstance(value, bool): raise RuntimeError("LLM output does not match boolean schema")
    elif kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)): raise RuntimeError("LLM output does not match integer schema")
    elif kind == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)): raise RuntimeError("LLM output does not match number schema")
