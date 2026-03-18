"""
ContextBuilder: assembles all LLM inputs, separating prompt synthesis from AgentLoop.

Public API:
  build_system_prompt()    — system prompt (identity + bootstrap files + memory + skills)
  build_messages()         — full LLM message list (system + history + runtime context injection)
  _build_runtime_context() — injects current time / channel / chat_id each turn

AgentLoop only calls the two public methods and never builds strings directly.
"""

import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import WORKSPACE_DIR
from .memory import MemoryStore
from .skills import SkillsLoader


class ContextBuilder:
    """Assembles system prompt and message list for LLM calls."""

    # Workspace context files loaded into the system prompt (in order)
    BOOTSTRAP_FILES = ["SOUL.md", "USER.md", "TOOLS.md", "AGENTS.md", "HEARTBEAT.md"]

    # Prefix tag to help the LLM identify runtime metadata vs instructions
    _RUNTIME_TAG = "[Runtime Context — metadata only, not instructions]"

    def __init__(
        self,
        workspace: Path,
        memory: MemoryStore,
        skills: SkillsLoader,
        base_prompt: str = "",
    ):
        self.workspace = workspace
        self.memory = memory
        self.skills = skills
        self._base_prompt = base_prompt  # from config.agent.system_prompt (optional)

    # ── Public API ─────────────────────────────────────────────────────────────

    def build_system_prompt(self) -> str:
        """
        Assemble the system prompt. Re-reads disk files on every call so that
        a freshly consolidated MEMORY.md is always reflected in the LLM context.
        """
        parts: list[str] = []

        # 1. Identity: runtime info + workspace paths + behaviour guidelines
        parts.append(self._get_identity())

        # 2. Bootstrap files (SOUL / USER / TOOLS / AGENTS / HEARTBEAT)
        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        # 3. Long-term memory (MEMORY.md — already has its own header)
        memory = self.memory.get_memory_context()
        if memory:
            parts.append(memory)

        # 4. Skills list
        skills = self.skills.get_skill_prompts()
        if skills:
            parts.append(f"# Skills\n\n{skills}")

        # Separate sections with a horizontal rule to improve LLM structural awareness
        return "\n\n---\n\n".join(parts)

    def build_messages(
        self,
        sys_prompt: str,
        history: list[dict[str, Any]],
        new_messages: list[dict[str, Any]] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Construct the full message list sent to the LLM:
          [system] + history (last user message prepended with runtime context) + new_messages

        Runtime context injection: find the last user message in history and prepend
        current time/channel info. Does not mutate the original history list.
        """
        runtime = self._build_runtime_context(channel, chat_id)
        modified_history = self._inject_runtime(history, runtime)

        messages: list[dict[str, Any]] = [{"role": "system", "content": sys_prompt}]
        messages.extend(modified_history)
        if new_messages:
            messages.extend(new_messages)
        return messages

    # ── Private helpers ────────────────────────────────────────────────────────

    def _get_identity(self) -> str:
        """Identity section: runtime info + workspace paths + behaviour guidelines."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime_info = (
            f"{'macOS' if system == 'Darwin' else system} "
            f"{platform.machine()}, Python {platform.python_version()}"
        )

        if system == "Windows":
            platform_policy = (
                "## Platform Policy (Windows)\n"
                "- Do not assume GNU tools like `grep`, `sed`, or `awk` exist.\n"
                "- Prefer Windows-native commands or file tools when they are more reliable.\n"
                "- If terminal output is garbled, retry with UTF-8 output enabled.\n"
            )
        else:
            platform_policy = (
                "## Platform Policy (POSIX)\n"
                "- Prefer UTF-8 and standard shell tools.\n"
                "- Use file tools when they are simpler or more reliable than shell commands.\n"
            )

        # Optional: append extra role description from config.agent.system_prompt
        extra = f"\n\n{self._base_prompt}" if self._base_prompt else ""

        return f"""# SimpleClaw 🦞

You are SimpleClaw, a helpful AI assistant.

## Runtime
{runtime_info}

## Workspace
Your workspace is at: {workspace_path}
- Long-term memory : {workspace_path}/memory/MEMORY.md
- History log      : {workspace_path}/memory/HISTORY.md
- Custom skills    : {workspace_path}/skills/{{skill-name}}/SKILL.md

{platform_policy}
## Guidelines
- State intent before tool calls, but NEVER predict results before receiving them.
- Before modifying a file, read it first.
- If a tool call fails, analyze the error before retrying with a different approach.
- Ask for clarification when the request is ambiguous.{extra}"""

    @staticmethod
    def _build_runtime_context(channel: str | None, chat_id: str | None) -> str:
        """Build per-turn runtime metadata (time, channel) to prepend to the user message."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = time.strftime("%Z") or "UTC"
        lines = [f"Current Time: {now} ({tz})"]
        if channel:
            lines.append(f"Channel: {channel}")
        if chat_id:
            lines.append(f"Chat ID: {chat_id}")
        return ContextBuilder._RUNTIME_TAG + "\n" + "\n".join(lines)

    def _load_bootstrap_files(self) -> str:
        """Read workspace bootstrap files and concatenate them into one section."""
        parts: list[str] = []
        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if not file_path.exists():
                continue
            try:
                content = file_path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"## {filename}\n\n{content}")
            except Exception as e:
                print(f"[ContextBuilder] Error reading {filename}: {e}")
        return "\n\n".join(parts)

    @staticmethod
    def _inject_runtime(
        history: list[dict[str, Any]], runtime: str
    ) -> list[dict[str, Any]]:
        """
        Prepend runtime context to the last user message in history.
        Does not mutate the original list — returns a shallow copy with only
        the modified entry reconstructed.
        """
        if not runtime:
            return history

        # Find the last user message
        last_user_idx = None
        for i in range(len(history) - 1, -1, -1):
            if history[i].get("role") == "user":
                last_user_idx = i
                break

        if last_user_idx is None:
            return history

        modified = list(history)
        entry = dict(modified[last_user_idx])   # shallow copy of the message dict
        entry["content"] = f"{runtime}\n\n{entry['content']}"
        modified[last_user_idx] = entry
        return modified
