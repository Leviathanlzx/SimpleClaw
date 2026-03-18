"""
HeartbeatService: Periodic background loop that wakes the agent to check HEARTBEAT.md.

Two-phase design:
  Phase 1 (decide): lightweight LLM call with virtual tool → skip or run
  Phase 2 (execute): publish InboundMessage to bus so the full agent loop handles it

This keeps heartbeat decoupled from the agent internals — it simply injects a
trigger message into the bus, and the agent handles it like any other message.
"""
import asyncio
import json
import datetime
from pathlib import Path

from .bus import MessageBus, InboundMessage
from .provider import LLMProvider

# Virtual tool used only by the decision call — never registered in ToolRegistry
_HEARTBEAT_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "heartbeat",
            "description": "Report heartbeat decision after reviewing HEARTBEAT.md tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["skip", "run"],
                        "description": "skip = no active tasks, run = has tasks that need attention",
                    },
                    "tasks": {
                        "type": "string",
                        "description": "Natural-language summary of the active tasks (required when action=run)",
                    },
                },
                "required": ["action"],
            },
        },
    }
]


class HeartbeatService:
    """
    Runs in the background, periodically reading HEARTBEAT.md.

    Phase 1: LLM decides skip/run via a virtual tool call (cheap, no full agent loop).
    Phase 2: If "run", publishes an InboundMessage to the bus so the full agent loop
             picks it up and handles it with all tools/memory available.
    """

    def __init__(
        self,
        workspace: Path,
        provider: LLMProvider,
        bus: MessageBus,
        interval_s: int = 1800,
        enabled: bool = True,
    ):
        self.workspace = workspace
        self.provider = provider
        self.bus = bus
        self.interval_s = interval_s
        self.enabled = enabled
        self._running = False

    @property
    def _heartbeat_file(self) -> Path:
        return self.workspace / "HEARTBEAT.md"

    def _read_heartbeat_file(self) -> str | None:
        if self._heartbeat_file.exists():
            try:
                return self._heartbeat_file.read_text(encoding="utf-8")
            except Exception:
                return None
        return None

    async def _decide(self, content: str) -> tuple[str, str]:
        """Phase 1: ask LLM to decide skip/run via the virtual heartbeat tool call.

        Returns (action, tasks) where action is 'skip' or 'run'.
        """
        response = await self.provider.chat(
            messages=[
                {
                    "role": "system",
                    "content": "You are a heartbeat agent. Call the heartbeat tool to report your decision.",
                },
                {
                    "role": "user",
                    "content": (
                        "Review the following HEARTBEAT.md and decide if there are active tasks to execute.\n\n"
                        f"{content}"
                    ),
                },
            ],
            tools=_HEARTBEAT_TOOL,
        )

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            return "skip", ""

        tc = tool_calls[0]
        if hasattr(tc, "function"):
            args_str = tc.function.arguments
        else:
            args_str = tc.get("arguments", "{}")

        try:
            args = json.loads(args_str) if isinstance(args_str, str) else args_str
        except Exception:
            args = {}

        return args.get("action", "skip"), args.get("tasks", "")

    async def _tick(self) -> None:
        """Single heartbeat tick: read → decide → maybe publish to bus."""
        content = self._read_heartbeat_file()
        if not content or not content.strip():
            print("[Heartbeat] HEARTBEAT.md missing or empty, skipping.")
            return

        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[Heartbeat] Checking tasks at {now_str}...")

        try:
            action, tasks = await self._decide(content)
        except Exception as e:
            print(f"[Heartbeat] Decision phase failed: {e}")
            return

        if action != "run":
            print("[Heartbeat] No active tasks. Going back to sleep.")
            return

        print(f"[Heartbeat] Active tasks found — waking agent: {tasks!r}")
        await self.bus.publish_inbound(
            InboundMessage(
                channel="heartbeat",
                chat_id="system",
                content=(
                    f"[HEARTBEAT {now_str}] You have background tasks to address:\n{tasks}"
                ),
                metadata={"type": "heartbeat"},
            )
        )

    async def start(self) -> None:
        if not self.enabled:
            print("[Heartbeat] Disabled via config.")
            return

        print(f"[Heartbeat] Service started (interval={self.interval_s}s).")
        self._running = True
        while self._running:
            await asyncio.sleep(self.interval_s)
            if self._running:
                await self._tick()

    def stop(self) -> None:
        self._running = False
