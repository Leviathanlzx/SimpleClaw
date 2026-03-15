import os
import json
import datetime
from pathlib import Path
from typing import List, Dict, Any

# ─────────────────────────────────────────
# 虚拟工具定义：让 LLM 强制返回结构化结果
# ─────────────────────────────────────────
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

# ─────────────────────────────────────────
# 粗略估算 token 数（字符数 / 4）
# ─────────────────────────────────────────
def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)

def _estimate_message_tokens(msg: dict) -> int:
    content = msg.get("content", "")
    if isinstance(content, str):
        return _estimate_tokens(content) + 4  # role + 固定开销
    return 10  # fallback


class MemoryStore:
    """
    管理两层持久化记忆：
      - MEMORY.md  : 长期事实（全量覆写，每次整合更新）
      - HISTORY.md : 时间线日志（追加，每次整合新增一条）
      - FULL_HISTORY.md : 完整审计日志（调试用，你的独特功能）

    新增：consolidate() 方法 — LLM 驱动的自动整合
      触发时机由 AgentLoop 控制（context 超 token 预算时）
    """

    # 连续失败多少次后降级为原始归档
    _MAX_FAILURES_BEFORE_RAW_ARCHIVE = 3

    def __init__(self, workspace: Path):
        self.memory_dir = workspace / "memory"
        self.history_dir = workspace / "history"
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.history_dir / "HISTORY.md"
        self.full_history_file = self.history_dir / "FULL_HISTORY.md"
        self._consecutive_failures = 0  # 新增：记录连续失败次数
        self._ensure_paths()

    def _ensure_paths(self):
        """Create directories and files if they do not exist."""
        # 1. Ensure directories exist
        if not self.memory_dir.exists():
            self.memory_dir.mkdir(parents=True)
            print(f"[Memory] Created memory directory: {self.memory_dir}")

        if not self.history_dir.exists():
            self.history_dir.mkdir(parents=True)
            print(f"[Memory] Created history directory: {self.history_dir}")

        # 2. Ensure files exist (touch)
        self._touch_file(self.memory_file, "# Long-Term Memory\n\n- No detailed facts stored yet.\n")
        self._touch_file(self.history_file, "# Conversation History\n\n")
        self._touch_file(self.full_history_file, "# Full Agent Interaction Log\n\n")

    def _touch_file(self, filepath: Path, default_content=""):
        if not filepath.exists():
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(default_content)

    # ─────────────────────────────────────────
    # 基础读写操作（与之前相同）
    # ─────────────────────────────────────────

    def load_long_term(self) -> str:
        """Read the content of MEMORY.md."""
        try:
            return self.memory_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def update_long_term(self, content: str):
        """Overwrite MEMORY.md with new consolidated facts."""
        with open(self.memory_file, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[Memory] Updated long-term memory (MEMORY.md)")

    def append_history_entry(self, entry: str):
        """
        【升级】追加一条整合摘要到 HISTORY.md。
        与旧的 append_history(role, content) 不同：
        这里直接写入 LLM 生成的整合条目字符串（格式已由 LLM 决定）。
        """
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")
        print(f"[Memory] Appended consolidation entry to HISTORY.md")

    def append_history(self, role: str, content: str):
        """
        旧版接口：直接记录单条对话到 HISTORY.md（供外部手动调用）。
        保留兼容性，但推荐使用 consolidate() 自动整合。
        """
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"**[{timestamp}] {role.title()}:**\n{content}\n\n---\n\n"
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry)

    def append_full_log(self, title: str, data: Any, format_type: str = "json"):
        """Log data to FULL_HISTORY.md for debugging/audit（你的独特调试功能，保持不变）。"""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        content = ""
        if format_type == "json":
            if isinstance(data, (dict, list)):
                try:
                    content = json.dumps(data, indent=2, default=str)
                except:
                    content = str(data)
            else:
                content = str(data)
            entry = f"\n## [{timestamp}] {title}\n```json\n{content}\n```\n"
        else:
            content = str(data)
            entry = f"\n## [{timestamp}] {title}\n\n{content}\n"

        try:
            with open(self.full_history_file, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            print(f"[Memory] Error logging to full history: {e}")

    def get_memory_context(self) -> str:
        """Compose a prompt section with memory — 用于拼入 System Prompt。"""
        long_term = self.load_long_term()
        return f"\n{long_term}\n" if long_term else ""

    # ─────────────────────────────────────────
    # 【新增核心功能】LLM 驱动的记忆整合
    # ─────────────────────────────────────────

    @staticmethod
    def _format_messages_for_consolidation(messages: list) -> str:
        """
        把消息列表格式化为易于 LLM 理解的文本。
        只包含 role 和 content，过滤工具调用细节。
        """
        lines = []
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content")
            # 跳过没有文本内容的消息（纯工具调用请求）
            if not content or not isinstance(content, str):
                continue
            timestamp = msg.get("timestamp", "")
            ts_prefix = f"[{timestamp[:16]}] " if timestamp else ""
            lines.append(f"{ts_prefix}{role.upper()}: {content}")
        return "\n".join(lines) if lines else "(no content)"

    async def consolidate(self, messages: list, provider) -> bool:
        """
        【核心新方法】使用 LLM 把一段旧对话整合进持久化记忆。

        工作流程：
          1. 读取当前 MEMORY.md（现有知识库）
          2. 构造 Prompt：现有记忆 + 待整合的对话段
          3. 独立调用 LLM，强制它使用 save_memory 工具返回结果
          4. 从工具调用中取出 history_entry 和 memory_update
          5. history_entry → 追加写入 HISTORY.md
          6. memory_update  → 覆写 MEMORY.md（如果有更新）

        返回值：
          True  = 整合成功，调用方可以推进 last_consolidated 游标
          False = 整合失败，调用方应稍后重试（不推进游标）
        """
        if not messages:
            return True  # 没有消息，视为成功

        current_memory = self.load_long_term()
        conversation_text = self._format_messages_for_consolidation(messages)

        # 构造整合 Prompt
        prompt = (
            f"## Current Long-term Memory\n"
            f"{current_memory or '(empty)'}\n\n"
            f"## Conversation to Process\n"
            f"{conversation_text}"
        )

        consolidation_messages = [
            {
                "role": "system",
                "content": (
                    "You are a memory consolidation agent. "
                    "Read the conversation and call the save_memory tool with your consolidation. "
                    "Preserve all existing facts in memory_update, and add new ones.\n"
                    "IMPORTANT: You MUST maintain the following Markdown structure for memory_update:\n"
                    "# Long-term Memory\n"
                    "## User Information\n"
                    "(facts about user)\n"
                    "## Preferences\n"
                    "(user preferences)\n"
                    "## Project Context\n"
                    "(ongoing projects facts)\n"
                    "## Important Notes\n"
                    "(other notes)\n"
                    "---\n"
                    "*This file is automatically updated...*\n"
                    "Do NOT remove these headers. Organize facts under the correct header."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        print(f"[Memory] Starting consolidation for {len(messages)} messages...")

        try:
            # 调用 LLM，传入 save_memory 工具定义
            # 注意：这是独立的 LLM 调用，不影响 Agent 主循环的 context
            response = await provider.chat(
                messages=consolidation_messages,
                tools=_SAVE_MEMORY_TOOL,
            )

            # 解析工具调用（兼容 OpenAI 对象风格和 Mock dict 风格）
            tool_calls = getattr(response, "tool_calls", None) or []

            if not tool_calls:
                # LLM 没有调用工具（可能只返回了文本）
                print(f"[Memory] Consolidation: LLM did not call save_memory tool")
                return self._handle_failure(messages)

            # 取第一个工具调用的参数
            first_call = tool_calls[0]
            if hasattr(first_call, "function"):
                # OpenAI 对象风格
                args_str = first_call.function.arguments
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
            elif isinstance(first_call, dict):
                # dict 风格（Mock 等）
                args = first_call.get("arguments", {})
                if isinstance(args, str):
                    args = json.loads(args)
            else:
                print(f"[Memory] Consolidation: unexpected tool_call format")
                return self._handle_failure(messages)

            # 验证必要字段
            if "history_entry" not in args or "memory_update" not in args:
                print(f"[Memory] Consolidation: save_memory missing required fields")
                return self._handle_failure(messages)

            history_entry = str(args["history_entry"]).strip()
            memory_update = str(args["memory_update"])

            if not history_entry:
                print(f"[Memory] Consolidation: history_entry is empty")
                return self._handle_failure(messages)

            # 写入 HISTORY.md（追加）
            self.append_history_entry(history_entry)

            # 写入 MEMORY.md（全量覆写，仅在内容有变化时）
            if memory_update != current_memory:
                self.update_long_term(memory_update)

            # 重置连续失败计数
            self._consecutive_failures = 0
            print(f"[Memory] Consolidation done for {len(messages)} messages")
            return True

        except Exception as e:
            print(f"[Memory] Consolidation exception: {e}")
            return self._handle_failure(messages)

    def _handle_failure(self, messages: list) -> bool:
        """
        处理整合失败：
          - 前 N 次失败返回 False（让调用方稍后重试）
          - 连续失败超过阈值后，降级为原始归档（不依赖 LLM），返回 True
        """
        self._consecutive_failures += 1
        print(f"[Memory] Consolidation failure #{self._consecutive_failures}")

        if self._consecutive_failures < self._MAX_FAILURES_BEFORE_RAW_ARCHIVE:
            return False  # 还未达到阈值，告知调用方失败（可重试）

        # 达到阈值：降级原始归档
        self._raw_archive(messages)
        self._consecutive_failures = 0
        return True  # 原始归档视为"成功"，游标可以推进

    def _raw_archive(self, messages: list):
        """
        降级兜底：不调用 LLM，直接把原始对话文本写入 HISTORY.md。
        确保即使 LLM 持续失败，旧消息也不会丢失。
        """
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        raw_text = self._format_messages_for_consolidation(messages)
        entry = f"[{ts}] [RAW ARCHIVE — {len(messages)} messages]\n{raw_text}"
        self.append_history_entry(entry)
        print(f"[Memory] Degraded to raw archive for {len(messages)} messages")
