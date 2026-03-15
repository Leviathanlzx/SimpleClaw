import os
from typing import Protocol, Any, List, Dict, Optional
import json

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

class LLMProvider(Protocol):
    """
    Abstract interface for LLM backends (OpenAI, Anthropic, Mock).
    """
    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Any:
        ...

class OpenAIProvider:
    """
    A real LLM provider using the OpenAI SDK (compatible with OpenRouter).
    """
    def __init__(self, api_key: str, base_url: str, model: str):
        if not AsyncOpenAI:
            raise ImportError("openai package is not installed. Please install it.")
        
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self.model = model

    async def chat(self, messages: list[dict], tools: list[dict] | None = None):
        kwargs = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
            
        try:
            response = await self.client.chat.completions.create(**kwargs)
            return response.choices[0].message
        except Exception as e:
            print(f"[Provider] Error calling LLM: {str(e)}")
            # Return a simple object with error message as content
            from types import SimpleNamespace
            return SimpleNamespace(content=f"Error: {e}", tool_calls=None)

class MockProvider:
    """
    A simulated LLM that detects keywords to trigger tools.
    """
    async def chat(self, messages: list[dict], tools: list[dict] | None = None):
        last_msg = messages[-1]["content"]
        
        # Simulate tool calling logic
        if "time" in last_msg.lower():
            return MockResponse(
                content=None,
                tool_calls=[SimpleToolCall(name="get_time", arguments={})]
            )
        
        # Simulate tool result processing
        if messages[-1]["role"] == "tool":
            return MockResponse(content=f"The time is {last_msg}. (LLM processed this)")

        # Default chat response
        return MockResponse(content=f"I heard you say: {last_msg}")

class MockResponse:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

class SimpleToolCall:
    def __init__(self, name, arguments):
        self.function = self
        self.name = name
        self.arguments = json.dumps(arguments)


