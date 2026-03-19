"""AgentLoop: the main think-act cycle.

Flow for each incoming message:
  1. Receive InboundMessage from bus
  2. Build system prompt (re-reads MEMORY.md to pick up latest consolidation)
  3. Check token budget → consolidate stale history if over limit
  4. Add user message to session
  5. Think-act inner loop: LLM → tool calls → LLM → … → final answer
  6. Save new messages (tool calls + results) to session and history
  7. Check token budget again (this turn may have grown the context)
  8. Publish OutboundMessage to bus
"""
import json

from .bus import MessageBus, OutboundMessage
from .provider import LLMProvider
from .memory import MemoryStore, Session, _estimate_message_tokens
from .skills import SkillsLoader
from .context import ContextBuilder
from .config import config, WORKSPACE_DIR


class AgentLoop:
    # Trigger consolidation when session tokens exceed this limit
    CONTEXT_WINDOW_TOKENS = 65536
    # After consolidation, aim to reduce to this fraction of the window
    CONSOLIDATION_TARGET_RATIO = 0.5
    # Max consolidation passes per message to prevent infinite loops
    MAX_CONSOLIDATION_ROUNDS = 5

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        tools,
        memory: MemoryStore,
        skills: SkillsLoader,
    ):
        self.bus = bus
        self.provider = provider
        self.tools = tools
        self.memory = memory

        # ContextBuilder handles all prompt assembly; AgentLoop never builds strings directly
        self.context_builder = ContextBuilder(
            workspace=WORKSPACE_DIR,
            memory=memory,
            skills=skills,
            base_prompt=config.agent.system_prompt,
        )

        # Single session (single-user mode).
        # For multi-user/multi-channel: replace with a SessionManager keyed by channel:chat_id.
        self.session = Session(key="cli:user1")

        # Restore full session from disk on startup
        for m in memory.load_full_session():
            self.session.messages.append(m)

        # Track last logged values to avoid writing identical entries to FULL_HISTORY.md
        self._last_sys_prompt = None
        self._last_tool_defs = None

    async def run(self):
        print("[Agent] Started.")
        while True:
            msg = await self.bus.consume_inbound()
            print(f"[Agent] Received from {msg.channel}: {msg.content}")

            # Build system prompt — re-reads MEMORY.md each time so consolidation is visible
            sys_prompt = self.context_builder.build_system_prompt()

            # Log to FULL_HISTORY.md only when something actually changed
            if sys_prompt != self._last_sys_prompt:
                self.memory.append_full_log("SYSTEM CHANGED", sys_prompt, format_type="markdown")
                self._last_sys_prompt = sys_prompt
            tool_defs = self.tools.get_definitions()
            tool_defs_str = json.dumps(tool_defs, sort_keys=True)
            if tool_defs_str != self._last_tool_defs:
                self.memory.append_full_log("TOOLS AVAILABLE", tool_defs, format_type="json")
                self._last_tool_defs = tool_defs_str
            self.memory.append_full_log("USER", msg.content, format_type="markdown")

            # Check budget before adding this turn (consolidate stale history if needed)
            await self._maybe_consolidate()

            self.session.add_message("user", msg.content)
            self.memory.append_history("user", msg.content)

            history = self.session.get_history()
            final_response, new_messages = await self._think_and_act(
                sys_prompt, history, channel=msg.channel, chat_id=msg.chat_id
            )

            # Save intermediate tool-call and tool-result messages to session
            for m in new_messages:
                if m.get("role") in ("assistant", "tool"):
                    # Carry through tool-specific fields (not all messages have them)
                    extra = {}
                    for key in ("tool_calls", "tool_call_id", "name"):
                        if key in m:
                            extra[key] = m[key]
                    self.session.add_message(m["role"], m.get("content") or "", **extra)
                    self.memory.append_history(m["role"], m.get("content") or "", tool_calls=m.get("tool_calls"), tool_name=m.get("name"))

            # Save the final assistant reply
            self.session.add_message("assistant", final_response)
            self.memory.append_history("assistant", final_response)
            self.memory.append_full_log("ASSISTANT", final_response, format_type="markdown")

            # Check budget again — this turn may have pushed us over the limit
            await self._maybe_consolidate()

            # Persist session to disk after every turn
            self.memory.save_session(self.session.messages)

            await self.bus.publish_outbound(
                OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=final_response)
            )

            current_tokens = self.session.estimate_tokens()
            print(f"[Agent] Context: {current_tokens}/{self.CONTEXT_WINDOW_TOKENS} tokens")

    async def _maybe_consolidate(self):
        """
        Consolidate session history if the token count exceeds CONTEXT_WINDOW_TOKENS.

        Each round finds a safe cut point (always at a user-turn boundary to keep
        conversation structure valid), passes the old messages to MemoryStore.consolidate(),
        then physically deletes those messages from session and history.json.
        """
        target = int(self.CONTEXT_WINDOW_TOKENS * self.CONSOLIDATION_TARGET_RATIO)

        for round_num in range(self.MAX_CONSOLIDATION_ROUNDS):
            estimated = self.session.estimate_tokens()
            if estimated < self.CONTEXT_WINDOW_TOKENS:
                return

            print(
                f"[Agent] Over budget ({estimated}/{self.CONTEXT_WINDOW_TOKENS}), "
                f"consolidation round {round_num + 1}..."
            )

            boundary = self._pick_consolidation_boundary(estimated - target)
            if boundary is None:
                print("[Agent] No safe consolidation boundary, skipping")
                return

            chunk = self.session.messages[:boundary]
            if not chunk:
                return

            print(f"[Agent] Consolidating {len(chunk)} messages (up to index {boundary})")
            success = await self.memory.consolidate(chunk, self.provider)

            if not success:
                print("[Agent] Consolidation failed, will retry next round")
                return

            # Physically remove consolidated messages from session and history.json
            self.session.messages = self.session.messages[boundary:]
            self.session.last_consolidated = 0
            self.memory.save_session(self.session.messages)
            print(f"[Agent] Removed {boundary} messages, tokens now: {self.session.estimate_tokens()}")

    def _pick_consolidation_boundary(self, tokens_to_remove: int) -> int | None:
        """
        Find the furthest safe cut point in the session history.

        Rules:
          - Must cut at a user-turn boundary (to keep assistant/tool pairs intact)
          - Must remove at least tokens_to_remove tokens
          - Always scans from index 0 (messages are physically deleted after consolidation)

        Returns the message index where the cut should happen, or None if not found.
        """
        messages = self.session.messages
        removed_tokens = 0
        last_safe_boundary = None

        for idx, msg in enumerate(messages):
            if idx > 0 and msg.get("role") == "user":
                last_safe_boundary = idx
                if removed_tokens >= tokens_to_remove:
                    return last_safe_boundary
            removed_tokens += _estimate_message_tokens(msg)

        return last_safe_boundary

    async def _think_and_act(
        self,
        sys_prompt: str,
        history: list,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> tuple[str, list]:
        """
        Inner loop: call the LLM, execute any tool calls, repeat until a final answer.

        Returns (final_response_text, list_of_intermediate_messages).
        Intermediate messages are the assistant tool-call requests and tool results
        produced during this turn — NOT including the final text reply.
        """
        def _build_tool_call_message(resp) -> dict:
            """Convert an LLM response with tool_calls into a storable dict."""
            msg = {"role": "assistant", "tool_calls": []}
            if getattr(resp, "content", None):
                msg["content"] = resp.content
            for tc in getattr(resp, "tool_calls", []):
                fn = getattr(tc, "function", None)
                if fn:
                    msg["tool_calls"].append({
                        "id": getattr(tc, "id", None),
                        "type": "function",
                        "function": {
                            "name": getattr(fn, "name", None),
                            "arguments": getattr(fn, "arguments", None),
                        },
                    })
            return msg

        new_messages = []

        for _ in range(config.agent.max_loops):
            messages = self.context_builder.build_messages(
                sys_prompt, history, new_messages, channel=channel, chat_id=chat_id
            )
            response = await self.provider.chat(messages, self.tools.get_definitions())
            tool_calls = getattr(response, "tool_calls", [])

            if not tool_calls:
                # No tool calls → this is the final answer
                return getattr(response, "content", "") or "Done.", new_messages

            # Record the assistant's tool-call request, then execute each tool
            assistant_msg = _build_tool_call_message(response)
            new_messages.append(assistant_msg)
            _log_calls = [
                {
                    "tool": tc["function"]["name"],
                    "args": json.loads(tc["function"]["arguments"])
                           if isinstance(tc["function"]["arguments"], str)
                           else tc["function"]["arguments"],
                }
                for tc in assistant_msg.get("tool_calls", [])
            ]
            self.memory.append_full_log("ASSISTANT_CALL", _log_calls, format_type="json")

            for tc in tool_calls:
                func_name = tc.function.name
                args_str = tc.function.arguments
                call_id = getattr(tc, "id", None)

                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except Exception:
                    args = {}

                print(f"[Agent] Tool: {func_name}")
                result = await self.tools.execute(func_name, args)

                tool_msg = {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": func_name,
                    "content": str(result),
                }
                new_messages.append(tool_msg)
                self.memory.append_full_log("TOOL_RESULT", {"tool": func_name, "result": str(result)}, format_type="json")

        return "Thinking loop limit reached.", new_messages
