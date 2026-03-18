import json
from types import SimpleNamespace
from typing import Protocol, Any

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

class LLMProvider(Protocol):
    """Protocol interface for LLM backends."""
    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Any: ...

class OpenAIProvider:
    """Real LLM provider using the OpenAI SDK (OpenRouter-compatible)."""

    def __init__(self, api_key: str, base_url: str, model: str):
        if not AsyncOpenAI:
            raise ImportError("openai package is not installed.")
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Any:
        kwargs = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            response = await self.client.chat.completions.create(**kwargs)
            return response.choices[0].message
        except Exception as e:
            print(f"[Provider] LLM error: {e}")
            return SimpleNamespace(content=f"Error: {e}", tool_calls=None)

class MockProvider:
    """Simulated LLM for testing — detects keywords to trigger tool calls."""

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Any:
        last = messages[-1]
        content = last.get("content", "")
        if last["role"] == "tool":
            return MockResponse(content=f"Tool returned: {content}")
        if "time" in content.lower():
            return MockResponse(tool_calls=[SimpleToolCall("get_time", {})])
        return MockResponse(content=f"Echo: {content}")

class MockResponse:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

class SimpleToolCall:
    """Minimal tool call object matching the OpenAI tool call interface."""
    def __init__(self, name: str, arguments: dict):
        self.function = self
        self.name = name
        self.arguments = json.dumps(arguments)


