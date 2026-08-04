"""Build the configured runtime registry without importing provider SDKs."""
from __future__ import annotations

from .ollama import OllamaRuntime
from .openai_compatible import OpenAICompatibleRuntime
from .openclaw import OpenClawRuntime
from .sidecars import DeerFlowRuntime, HermesRuntime
from .registry import RuntimeRegistry


def default_registry(*, ollama_enabled: bool | None = None, omniroute_enabled: bool | None = None, hermes_enabled: bool | None = None, deerflow_enabled: bool | None = None, openclaw_enabled: bool | None = None) -> RuntimeRegistry:
    return RuntimeRegistry({
        "ollama": OllamaRuntime(enabled=ollama_enabled),
        "openai_compatible": OpenAICompatibleRuntime(enabled=omniroute_enabled),
        "hermes": HermesRuntime(enabled=hermes_enabled),
        "deerflow": DeerFlowRuntime(enabled=deerflow_enabled),
        "openclaw": OpenClawRuntime(enabled=openclaw_enabled),
    })
