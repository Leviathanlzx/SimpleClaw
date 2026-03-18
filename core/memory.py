"""Two-layer persistent memory for the agent.

Storage layout (under workspace/):
  memory/MEMORY.md        — long-term facts (overwritten on each consolidation)
  history/HISTORY.md      — timestamped consolidation summaries (append-only)
  history/FULL_HISTORY.md — full debug log of every LLM exchange (append-only)

Memory consolidation (consolidate()):
  Called by AgentLoop when the session token budget is exceeded.
  A separate LLM call summarises the old messages into MEMORY.md and HISTORY.md,
  allowing the agent to forget the raw transcript while retaining key facts.
"""
import json
import datetime
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field


# ── Session ────────────────────────────────────────────────────────────────────

@dataclass
class Session:
    """
    In-memory conversation history with a consolidation cursor.

    Design:
      - messages is append-only (never deleted, friendly to LLM prompt caching)
      - last_consolidated is a cursor: messages[:cursor] have been summarised
        into MEMORY.md and are excluded from the active LLM context window
      - get_history() returns only messages[last_consolidated:], always
        starting from a user turn to keep conversation structure valid
    """
    key: str
    messages: list = field(default_factory=list)
    last_consolidated: int = 0  # index of first un-consolidated message

    def add_message(self, role: str, content: str, **extra):
        """Append a message with the current timestamp."""
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.datetime.now().isoformat(),
            **extra,
        })

    def get_history(self) -> list:
        """Return un-consolidated messages, starting from the first user turn."""
        unconsolidated = self.messages[self.last_consolidated:]
        # Always start from a user message to avoid orphaned tool-result blocks
        for i, m in enumerate(unconsolidated):
            if m.get("role") == "user":
                return unconsolidated[i:]
        return []

    def estimate_tokens(self) -> int:
        """Rough token count for the current active history."""
        return sum(_estimate_message_tokens(m) for m in self.get_history())


# ── Token estimation ───────────────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """Approximate: 1 token ≈ 4 characters."""
    return max(1, len(text) // 4)

def _estimate_message_tokens(msg: dict) -> int:
    content = msg.get("content", "")
    return (_estimate_tokens(content) + 4) if isinstance(content, str) else 10


# ── Virtual tool for consolidation ────────────────────────────────────────────
# Passed to the LLM during consolidation to force structured JSON output.
# Not registered in ToolRegistry — only used internally by consolidate().

_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": (
                            "A paragraph summarizing key events/decisions/topics from this conversation. "
                            "Start with [YYYY-MM-DD HH:MM]. Include enough detail to be useful when grepping."
                        ),
                    },
                    "memory_update": {
                        "type": "string",
                        "description": (
                            "Full updated long-term memory as markdown. "
                            "Include ALL existing facts plus new ones. Return unchanged if nothing new to add."
                        ),
                    },
                },
                "required": ["history_entry", "memory_update"],
            },
        },
    }
]


# ── MemoryStore ────────────────────────────────────────────────────────────────

class MemoryStore:
    """
    Manages the two-layer persistent memory files.

    Public interface:
      load_long_term()         — read MEMORY.md
      update_long_term()       — overwrite MEMORY.md
      append_history_entry()   — append a consolidation summary to HISTORY.md
      append_history()         — append a raw turn to HISTORY.md
      append_full_log()        — append a debug entry to FULL_HISTORY.md
      get_memory_context()     — return MEMORY.md content for the system prompt
      consolidate()            — LLM-driven consolidation of a message chunk
    """

    # Fall back to raw archiving after this many consecutive LLM failures
    _MAX_FAILURES_BEFORE_RAW_ARCHIVE = 3

    def __init__(self, workspace: Path):
        self.memory_dir = workspace / "memory"
        self.history_dir = workspace / "history"
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.history_dir / "HISTORY.md"
        self.full_history_file = self.history_dir / "FULL_HISTORY.md"
        self._consecutive_failures = 0
        self._ensure_paths()

    def _ensure_paths(self):
        """Create directories and seed empty files on first run."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        if not self.memory_file.exists():
            self.memory_file.write_text("# Long-Term Memory\n\n- No detailed facts stored yet.\n", encoding="utf-8")
        if not self.history_file.exists():
            self.history_file.write_text("# Conversation History\n\n", encoding="utf-8")
        if not self.full_history_file.exists():
            self.full_history_file.write_text("# Full Agent Interaction Log\n\n", encoding="utf-8")

    # ── Basic read/write ───────────────────────────────────────────────────────

    def load_long_term(self) -> str:
        """Read MEMORY.md content."""
        try:
            return self.memory_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def update_long_term(self, content: str):
        """Overwrite MEMORY.md with consolidated facts."""
        self.memory_file.write_text(content, encoding="utf-8")
        print("[Memory] Updated MEMORY.md")

    def append_history_entry(self, entry: str):
        """Append a consolidation summary paragraph to HISTORY.md."""
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")
        print("[Memory] Appended entry to HISTORY.md")

    def append_history(self, role: str, content: str):
        """Append a single raw turn to HISTORY.md."""
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"**[{ts}] {role.title()}:**\n{content}\n\n---\n\n"
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry)

    def append_full_log(self, title: str, data: Any, format_type: str = "json"):
        """Append a timestamped debug entry to FULL_HISTORY.md."""
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        if format_type == "json":
            try:
                body = json.dumps(data, indent=2, default=str) if isinstance(data, (dict, list)) else str(data)
            except Exception:
                body = str(data)
            entry = f"\n## [{ts}] {title}\n```json\n{body}\n```\n"
        else:
            entry = f"\n## [{ts}] {title}\n\n{data}\n"
        try:
            with open(self.full_history_file, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            print(f"[Memory] Error writing full log: {e}")

    def get_memory_context(self) -> str:
        """Return MEMORY.md content for inclusion in the system prompt."""
        return self.load_long_term().strip()

    # ── LLM-driven consolidation ───────────────────────────────────────────────

    @staticmethod
    def _format_messages_for_consolidation(messages: list) -> str:
        """Format a message list into plain text for LLM consolidation."""
        lines = []
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content")
            if not content or not isinstance(content, str):
                continue  # skip tool-call-only messages with no text
            ts = msg.get("timestamp", "")
            prefix = f"[{ts[:16]}] " if ts else ""
            lines.append(f"{prefix}{role.upper()}: {content}")
        return "\n".join(lines) or "(no content)"

    async def consolidate(self, messages: list, provider) -> bool:
        """
        Summarise a chunk of old messages into MEMORY.md and HISTORY.md via LLM.

        Workflow:
          1. Read current MEMORY.md (existing knowledge base)
          2. Prompt the LLM with current memory + the message chunk
          3. LLM must call the save_memory virtual tool with its output
          4. Write history_entry → HISTORY.md (append)
             Write memory_update → MEMORY.md (overwrite if changed)

        Returns True on success (caller may advance the consolidation cursor).
        Returns False on failure (caller should retry later).
        """
        if not messages:
            return True

        current_memory = self.load_long_term()
        conversation_text = self._format_messages_for_consolidation(messages)

        consolidation_messages = [
            {
                "role": "system",
                "content": (
                    "You are a memory consolidation agent. "
                    "Read the conversation and call the save_memory tool with your consolidation. "
                    "Preserve all existing facts in memory_update, and add new ones.\n"
                    "IMPORTANT: You MUST maintain the following Markdown structure for memory_update:\n"
                    "# Long-term Memory\n"
                    "## User Information\n(facts about user)\n"
                    "## Preferences\n(user preferences)\n"
                    "## Project Context\n(ongoing projects facts)\n"
                    "## Important Notes\n(other notes)\n"
                    "---\n*This file is automatically updated...*\n"
                    "Do NOT remove these headers. Organize facts under the correct header."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"## Current Long-term Memory\n{current_memory or '(empty)'}\n\n"
                    f"## Conversation to Process\n{conversation_text}"
                ),
            },
        ]

        print(f"[Memory] Consolidating {len(messages)} messages...")
        try:
            response = await provider.chat(messages=consolidation_messages, tools=_SAVE_MEMORY_TOOL)
            tool_calls = getattr(response, "tool_calls", None) or []

            if not tool_calls:
                print("[Memory] LLM did not call save_memory tool")
                return self._handle_failure(messages)

            tc = tool_calls[0]
            if not hasattr(tc, "function"):
                print("[Memory] Unexpected tool_call format")
                return self._handle_failure(messages)
            args_str = tc.function.arguments
            args = json.loads(args_str) if isinstance(args_str, str) else args_str

            if "history_entry" not in args or "memory_update" not in args:
                print("[Memory] save_memory missing required fields")
                return self._handle_failure(messages)

            history_entry = str(args["history_entry"]).strip()
            memory_update = str(args["memory_update"])

            if not history_entry:
                print("[Memory] history_entry is empty")
                return self._handle_failure(messages)

            self.append_history_entry(history_entry)
            if memory_update != current_memory:
                self.update_long_term(memory_update)

            self._consecutive_failures = 0
            print(f"[Memory] Consolidation complete ({len(messages)} messages)")
            return True

        except Exception as e:
            print(f"[Memory] Consolidation error: {e}")
            return self._handle_failure(messages)

    def _handle_failure(self, messages: list) -> bool:
        """
        Track consecutive failures. After _MAX_FAILURES_BEFORE_RAW_ARCHIVE failures,
        fall back to raw archiving so old messages can still be cleared from context.
        """
        self._consecutive_failures += 1
        print(f"[Memory] Failure #{self._consecutive_failures}")
        if self._consecutive_failures < self._MAX_FAILURES_BEFORE_RAW_ARCHIVE:
            return False  # signal failure so caller retries later
        # Too many failures: archive the raw text and advance the cursor anyway
        self._raw_archive(messages)
        self._consecutive_failures = 0
        return True

    def _raw_archive(self, messages: list):
        """Fallback: save raw message text to HISTORY.md without LLM processing."""
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        raw_text = self._format_messages_for_consolidation(messages)
        self.append_history_entry(f"[{ts}] [RAW ARCHIVE — {len(messages)} messages]\n{raw_text}")
        print(f"[Memory] Raw archive written ({len(messages)} messages)")
