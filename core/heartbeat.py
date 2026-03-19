"""
HeartbeatService: Periodic background loop that wakes the agent to check HEARTBEAT.md.

Two-phase design:
  Phase 1 (decide): lightweight LLM call with virtual tool → skip or run
  Phase 2 (execute): call agent.process_direct() with an independent session,
                     then deliver the response to the best available channel.

This keeps heartbeat decoupled from the agent internals — it simply invokes the
agent via callback, and the wiring in main.py handles outbound delivery.
"""
import asyncio
import json
import datetime
from pathlib import Path
from typing import Callable, Awaitable

from .bus import MessageBus, OutboundMessage
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

# Type alias for the agent callback: async fn(content, session_key, channel, chat_id) -> str
ProcessDirectCallback = Callable[[str, str, str, str], Awaitable[str]]
# Type alias for listing sessions: fn() -> list[dict]
ListSessionsCallback = Callable[[], list[dict]]


class HeartbeatService:
    """
    Runs in the background, periodically reading HEARTBEAT.md.

    Phase 1: LLM decides skip/run via a virtual tool call (cheap, no full agent loop).
    Phase 2: If "run", calls agent.process_direct() with the best target channel,
             then publishes the response to that channel's outbound queue.
    """

    def __init__(
        self,
        workspace: Path,
        provider: LLMProvider,
        bus: MessageBus,
        interval_s: int = 1800,
        enabled: bool = True,
        process_direct: ProcessDirectCallback | None = None,
        list_sessions: ListSessionsCallback | None = None,
        enabled_channels: set[str] | None = None,
    ):
        self.workspace = workspace
        self.provider = provider
        self.bus = bus
        self.interval_s = interval_s
        self.enabled = enabled
        self._process_direct = process_direct
        self._list_sessions = list_sessions
        self._enabled_channels = enabled_channels or set()
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

    def _pick_target(self) -> tuple[str, str]:
        """
        Smart target selection: find the best channel to deliver heartbeat results.

        Prefers the most recently active external channel (telegram, wecom) over CLI,
        since CLI users may not be watching the terminal.
        """
        if self._list_sessions:
            for item in self._list_sessions():
                key = item.get("key", "")
                if ":" not in key:
                    continue
                channel, chat_id = key.split(":", 1)
                # Skip internal channels
                if channel in ("cli", "system", "heartbeat", "cron"):
                    continue
                # Only route to enabled channels
                if channel in self._enabled_channels and chat_id:
                    return channel, chat_id

        # Fallback to CLI
        return "cli", "user1"

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
        """Single heartbeat tick: read → decide → maybe execute via callback."""
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

        print(f"[Heartbeat] Active tasks found — executing: {tasks!r}")

        # Phase 2: execute via agent callback
        if not self._process_direct:
            # Fallback: publish to bus inbound (old behavior)
            print("[Heartbeat] WARNING: No process_direct callback, falling back to bus.")
            from .bus import InboundMessage
            await self.bus.publish_inbound(InboundMessage(
                channel="heartbeat", chat_id="system",
                content=f"[HEARTBEAT {now_str}] You have background tasks to address:\n{tasks}",
                metadata={"type": "heartbeat"},
            ))
            return

        # Pick best target channel
        channel, chat_id = self._pick_target()
        print(f"[Heartbeat] Target: {channel}:{chat_id}")

        try:
            response = await self._process_direct(
                f"[HEARTBEAT {now_str}] You have background tasks to address:\n{tasks}",
                "heartbeat:system",       # independent session key
                channel,
                chat_id,
            )
        except Exception as e:
            print(f"[Heartbeat] Execution failed: {e}")
            return

        if not response:
            return

        # Deliver the response to the target channel
        if channel == "cli":
            # CLI: just print, no bus publish needed
            print(f"\n[Heartbeat] > Agent: {response}\n")
        else:
            # External channel: publish to outbound bus for delivery
            await self.bus.publish_outbound(OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=response,
            ))
            print(f"[Heartbeat] Response delivered to {channel}:{chat_id}")

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
