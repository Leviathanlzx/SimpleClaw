"""
The Agent Loop:
1. Wait for InboundMessage.
2. Build Context (history from Session + memory from MEMORY.md).
3. Check token budget — consolidate old messages if context is too long.
4. Call LLM.
5. If Tool Call -> Execute Tool -> Loop Back to LLM.
6. If Final Answer -> Save turn to Session, publish OutboundMessage.
7. Check token budget again (this turn may have grown the context).
"""
import json
import datetime
from dataclasses import dataclass, field
from pathlib import Path

from .bus import MessageBus, OutboundMessage
from .provider import LLMProvider
from .memory import MemoryStore, Session, _estimate_tokens, _estimate_message_tokens
from .skills import SkillsLoader
from .config import config, WORKSPACE_DIR

# ─────────────────────────────────────────
# AgentLoop：主循环（已升级记忆整合）
# ─────────────────────────────────────────

class AgentLoop:
    # Token 预算：超出这个值就触发整合
    # 调整为 32k，平衡上下文长度与处理速度
    CONTEXT_WINDOW_TOKENS = 8192
    # 整合目标：压缩到窗口的一半
    CONSOLIDATION_TARGET_RATIO = 0.5
    # 每次处理时最多进行几轮整合
    MAX_CONSOLIDATION_ROUNDS = 5

    def __init__(self, bus: MessageBus, provider: LLMProvider, tools, memory: MemoryStore, skills: SkillsLoader):
        self.bus = bus
        self.provider = provider
        self.tools = tools
        self.memory = memory
        self.skills = skills
        self.system_prompt = config.agent.system_prompt

        # 升级：单一 Session（对应 CLI 单通道）
        # 如果未来支持多通道，每个 channel:chat_id 独立一个 Session
        self.session = Session(key="cli:user1")

        # 调试日志辅助（保留你的原有功能）
        self._last_sys_prompt = None
        self._last_tool_defs = None

    async def run(self):
        print("[Agent] Started thinking loop...")
        while True:
            # 1. 等待消息（Ingress）
            msg = await self.bus.consume_inbound()
            print(f"[Agent] Received from {msg.channel}: {msg.content}")

            # 2. 构建 System Prompt（每次构建确保 MEMORY.md 最新内容被读入）
            sys_prompt = self._build_system_context()

            # 调试日志：系统提示有变化时记录（保留你的原有功能）
            if sys_prompt != self._last_sys_prompt:
                self.memory.append_full_log("SYSTEM CHANGED", sys_prompt, format_type="markdown")
                self._last_sys_prompt = sys_prompt

            # 工具定义变化时记录
            tool_defs = self.tools.get_definitions()
            tool_defs_str = json.dumps(tool_defs, sort_keys=True)
            if tool_defs_str != self._last_tool_defs:
                self.memory.append_full_log("TOOLS AVAILABLE", tool_defs, format_type="json")
                self._last_tool_defs = tool_defs_str

            self.memory.append_full_log("USER", msg.content, format_type="markdown")

            # 3. 处理消息前：检测 token 预算，必要时整合旧消息
            await self._maybe_consolidate()

            # 4. 把用户消息加入 Session（追加，不修改）
            self.session.add_message("user", msg.content)
            self.memory.append_history("user", msg.content)

            # 5. 思考 & 行动循环
            history = self.session.get_history()
            final_response, new_messages = await self._think_and_act(sys_prompt, history)

            # 6. 把本轮新产生的 assistant/tool 消息保存到 Session
            #    （注意：user 消息已经在步骤4加入，这里只存新增的部分）
            for m in new_messages:
                role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
                if role in ("assistant", "tool"):
                    content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
                    extra = {}
                    # 保留工具调用信息
                    if isinstance(m, dict):
                        if "tool_calls" in m:
                            extra["tool_calls"] = m["tool_calls"]
                        if "tool_call_id" in m:
                            extra["tool_call_id"] = m["tool_call_id"]
                        if "name" in m:
                            extra["name"] = m["name"]
                    self.session.add_message(role, content or "", **extra)
                    self.memory.append_history(role, content or "")

            # 把最终回复加入 Session 和 History
            self.session.add_message("assistant", final_response)
            self.memory.append_history("assistant", final_response)

            # 记录最终响应
            self.memory.append_full_log("ASSISTANT", final_response, format_type="markdown")

            # 7. 处理消息后：再次检测（本轮新消息可能让 context 超出预算）
            await self._maybe_consolidate()

            # 8. 发送响应（Egress）
            out_msg = OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=final_response
            )
            await self.bus.publish_outbound(out_msg)

            # 9. 显示当前 Token 使用情况
            current_tokens = self.session.estimate_tokens()
            print(f"[Agent] Session Context: {current_tokens} / {self.CONTEXT_WINDOW_TOKENS} tokens")

    async def _maybe_consolidate(self):
        """
        检测 token 预算，超出时触发记忆整合。
        整合成功后推进 Session.last_consolidated 游标。

        整合逻辑：
          1. 估算当前未整合历史的 token 数
          2. 如果未超出 CONTEXT_WINDOW_TOKENS，直接返回
          3. 寻找合适的切割边界（必须在 user 轮处切割）
          4. 把切割点前的消息发给 LLM 整合
          5. 整合成功 → 推进游标
          6. 循环，直到 token 数降到目标以下（或达最大轮次）
        """
        target = int(self.CONTEXT_WINDOW_TOKENS * self.CONSOLIDATION_TARGET_RATIO)

        for round_num in range(self.MAX_CONSOLIDATION_ROUNDS):
            estimated = self.session.estimate_tokens()

            if estimated < self.CONTEXT_WINDOW_TOKENS:
                return  # token 数在预算内，不需要整合

            print(
                f"[Agent] Token budget exceeded ({estimated}/{self.CONTEXT_WINDOW_TOKENS}), "
                f"starting consolidation round {round_num + 1}..."
            )

            # 寻找整合边界
            boundary = self._pick_consolidation_boundary(estimated - target)
            if boundary is None:
                print("[Agent] No safe consolidation boundary found, skipping")
                return

            # 取出需要整合的消息块
            chunk = self.session.messages[self.session.last_consolidated : boundary]
            if not chunk:
                return

            print(f"[Agent] Consolidating {len(chunk)} messages (up to index {boundary})")

            # 调用 MemoryStore.consolidate() 进行 LLM 整合
            success = await self.memory.consolidate(chunk, self.provider)

            if not success:
                print("[Agent] Consolidation failed, will retry next time")
                return  # 失败了不推进游标，等下次

            # 整合成功：推进游标（旧消息不再进入 LLM context）
            self.session.last_consolidated = boundary
            print(f"[Agent] Cursor advanced to {boundary}, new estimated tokens: {self.session.estimate_tokens()}")

    def _pick_consolidation_boundary(self, tokens_to_remove: int) -> int | None:
        """
        寻找安全的切割边界：必须在 user 消息处切割（保证对话轮次完整性）。

        从 last_consolidated 开始向后扫描，找到累计可移除 token 数达标的 user 轮位置。

        返回值：切割点的消息索引（从该索引开始的消息留在历史里），None 表示找不到。
        """
        start = self.session.last_consolidated
        messages = self.session.messages

        removed_tokens = 0
        last_safe_boundary = None

        for idx in range(start, len(messages)):
            msg = messages[idx]

            # 必须在 user 轮处切割
            if idx > start and msg.get("role") == "user":
                last_safe_boundary = idx
                # 已经移除足够多的 token，立即返回
                if removed_tokens >= tokens_to_remove:
                    return last_safe_boundary

            removed_tokens += _estimate_message_tokens(msg)

        # 返回能移除最多 token 的最后一个安全边界
        return last_safe_boundary

    async def _think_and_act(self, sys_prompt: str, history: list):
        """
        内循环：LLM → Tool → LLM … → Final Answer

        参数：
          sys_prompt : 构建好的系统提示（包含 MEMORY.md 内容）
          history    : 来自 session.get_history() 的未整合消息列表

        返回值：
          (final_response: str, new_messages: list) 
          new_messages 是本轮新产生的所有 assistant/tool 消息
        """
        def _to_assistant_tool_call_message(resp):
            msg_dict = {"role": "assistant", "tool_calls": []}
            if getattr(resp, "content", None):
                msg_dict["content"] = resp.content
            for tc in getattr(resp, "tool_calls", []):
                fn = getattr(tc, "function", None)
                if not fn:
                    continue
                msg_dict["tool_calls"].append({
                    "id": getattr(tc, "id", None),
                    "type": "function",
                    "function": {
                        "name": getattr(fn, "name", None),
                        "arguments": getattr(fn, "arguments", None),
                    },
                })
            return msg_dict

        # 本轮产生的新消息（用于回存到 Session）
        new_messages = []

        max_loops = config.agent.max_loops
        for _ in range(max_loops):  # 最大迭代次数，从配置读取
            # 构造发给 LLM 的消息列表
            messages = [{"role": "system", "content": sys_prompt}]
            messages.extend(history)         # 来自 Session 的历史（已整合后的部分）
            messages.extend(new_messages)    # 本轮工具调用产生的中间消息

            # 调用 LLM
            response = await self.provider.chat(messages, self.tools.get_definitions())

            tool_calls = getattr(response, "tool_calls", [])

            if tool_calls:
                # 记录 assistant 的工具调用请求
                assistant_call_msg = _to_assistant_tool_call_message(response)
                new_messages.append(assistant_call_msg)
                self.memory.append_full_log("ASSISTANT_CALL", assistant_call_msg, format_type="json")

                for tool_call in tool_calls:
                    # 解析工具调用（兼容 OpenAI 对象和 dict 风格）
                    if hasattr(tool_call, "function"):
                        func_name = tool_call.function.name
                        args_str = tool_call.function.arguments
                        call_id = tool_call.id
                    else:
                        func_name = tool_call["name"]
                        args_str = tool_call["arguments"]
                        call_id = "mock_id"

                    try:
                        args = json.loads(args_str) if isinstance(args_str, str) else args_str
                    except:
                        args = {}

                    print(f"[Agent] Calling tool: {func_name}")
                    result = await self.tools.execute(func_name, args)

                    tool_message = {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": func_name,
                        "content": str(result),
                    }
                    new_messages.append(tool_message)
                    self.memory.append_full_log("TOOL", tool_message, format_type="json")

                continue

            # 最终回复
            content = getattr(response, "content", "") or "Done."
            return content, new_messages

        return "Thinking loop limit reached.", new_messages

    def _build_system_context(self):
        """
        构建 System Prompt。
        关键：每次调用都重新读取 MEMORY.md，确保整合后的最新记忆被带入。
        """
        context = []

        # 1. 基础系统提示
        context.append(self.system_prompt)

        # 2. 工作区 Markdown 文件（Soul、User 等）
        for file_name in ["SOUL.md", "USER.md", "TOOLS.md", "AGENTS.md", "HEARTBEAT.md"]:
            file_path = WORKSPACE_DIR / file_name
            if file_path.exists():
                try:
                    content = file_path.read_text(encoding="utf-8").strip()
                    if content:
                        context.append(f"\n{content}\n")
                except Exception as e:
                    print(f"[Agent] Error reading {file_name}: {e}")

        # 3. 长期记忆（整合结果在这里进入对话）
        #    每次构建时重新读取 MEMORY.md，整合后的事实自动注入
        mem = self.memory.get_memory_context()
        if mem:
            context.append(mem)

        # 4. 技能
        skills = self.skills.get_skill_prompts()
        if skills:
            context.append(skills)

        return "\n\n".join(context)
