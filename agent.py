"""
The Agent Loop:
1. Wait for InboundMessage.
2. Build Context (simplified history here).
3. Call LLM.
4. If Tool Call -> Execute Tool -> Loop Back to LLM.
5. If Final Answer -> Publish OutboundMessage.
"""
import json
from bus import MessageBus, OutboundMessage
from provider import LLMProvider
from memory import MemoryStore
from skills import SkillsLoader
from config import config, WORKSPACE_DIR

class AgentLoop:
    def __init__(self, bus: MessageBus, provider: LLMProvider, tools, memory: MemoryStore, skills: SkillsLoader):
        self.bus = bus
        self.provider = provider
        self.tools = tools
        self.memory = memory
        self.skills = skills
        self.system_prompt = config.get("agent.system_prompt", "You are a helpful AI assistant.")
        self.history = []  # Short-term conversation context
        self._last_sys_prompt = None # Track last logged system prompt
        self._last_tool_defs = None # Track last logged tool definitions

    async def run(self):
        print("[Agent] Started thinking loop...")
        while True:
            # 1. Wait for message (Ingress)
            msg = await self.bus.consume_inbound()
            print(f"[Agent] Received from {msg.channel}: {msg.content}")

            # --- LOGGING CONTEXT (Before User) ---
            # 2a. System Prompt
            sys_prompt = self._build_system_context()
            if sys_prompt != self._last_sys_prompt:
                self.memory.append_full_log("SYSTEM CHANGED", sys_prompt, format_type="markdown")
                self._last_sys_prompt = sys_prompt

            # 2b. Tools
            tool_defs = self.tools.get_definitions()
            tool_defs_str = json.dumps(tool_defs, sort_keys=True)
            if tool_defs_str != self._last_tool_defs:
                self.memory.append_full_log("TOOLS AVAILABLE", tool_defs, format_type="json")
                self._last_tool_defs = tool_defs_str

            # 2c. Log User Message to Full History
            self.memory.append_full_log("USER", msg.content, format_type="markdown")

            # 3. Update Conversation History
            self.history.append({"role": "user", "content": msg.content})
            self.memory.append_history("user", msg.content)
            
            # 4. Think & Act Loop
            final_response = await self._think_and_act(sys_prompt)

            # 5. Respond (Egress)
            self.history.append({"role": "assistant", "content": final_response})
            self.memory.append_history("assistant", final_response)
            
            # Log Assistant Response to Full History
            self.memory.append_full_log("ASSISTANT", final_response, format_type="markdown")
            
            out_msg = OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=final_response
            )
            await self.bus.publish_outbound(out_msg)

    async def _think_and_act(self, sys_prompt: str):
        """
        The inner loop: LLM -> Tool -> LLM ... -> Final Answer
        """

        for i in range(5):  # Max iterations
            # Construct messages with Memory and Skills context
            messages = [{"role": "system", "content": sys_prompt}]
            messages.extend(self.history[-10:])

            # --- Log the exact request context sent to LLM ---
            # We filter/format purely for logging visibility
            log_messages = []
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "system":
                    # Don't spam the full system prompt every time
                    log_messages.append({"role": "system", "content": "(See SYSTEM CHANGED log for full content)"})
                    continue
                
                # Convert partial objects (like OpenAI Message objects) to dict for readability
                if hasattr(m, 'tool_calls'):
                    msg_dict = {"role": "assistant"}
                    if getattr(m, 'content', None):
                         msg_dict["content"] = m.content
                    tc_list = getattr(m, 'tool_calls', [])
                    if tc_list:
                        msg_dict["tool_calls"] = []
                        for tc in tc_list:
                            # Handle wrapped objects
                            fn = getattr(tc, 'function', None)
                            if fn:
                                msg_dict["tool_calls"].append({
                                    "id": getattr(tc, 'id', None),
                                    "type": "function",
                                    "function": {
                                        "name": getattr(fn, 'name', None),
                                        "arguments": getattr(fn, 'arguments', None)
                                    }
                                })
                    log_messages.append(msg_dict)
                else:
                    log_messages.append(m)
            
            self.memory.append_full_log(f"LLM REQUEST (Step {i+1})", log_messages, format_type="json")

            # Call LLM
            response = await self.provider.chat(messages, self.tools.get_definitions())

            # Check for tool calls (OpenAI object style)
            tool_calls = getattr(response, 'tool_calls', [])

            if tool_calls:
                # Add the assistant's request to history
                # We need to serialize tool_calls properly for history if using real API
                # For simplified demo, we just append the object if provider supports it, or dict
                self.history.append(response)

                for tool_call in tool_calls:
                    # Handle both object (OpenAI) and dict (Mock)
                    if hasattr(tool_call, 'function'):
                        func_name = tool_call.function.name
                        args_str = tool_call.function.arguments
                        call_id = tool_call.id
                    else:
                        func_name = tool_call['name']
                        args_str = tool_call['arguments']
                        call_id = "mock_id"

                    try:
                        args = json.loads(args_str) if isinstance(args_str, str) else args_str
                    except:
                        args = {}
                    
                    print(f"[Agent] Calling tool: {func_name}")
                    
                    # Log Tool Call - Full Raw JSON structure as agent sees it
                    raw_tool_call = {
                        "name": func_name,
                        "arguments": args_str # Keep original string or object
                    }
                    if hasattr(tool_call, 'id'):
                        raw_tool_call["id"] = tool_call.id
                    
                    self.memory.append_full_log(f"TOOL_CALL: {func_name}", raw_tool_call, format_type="json")

                    result = await self.tools.execute(func_name, args)

                    # Log Tool Output
                    self.memory.append_full_log(f"TOOL_OUTPUT: {func_name}", result, format_type="markdown")

                    # Add result to history
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": func_name,
                        "content": str(result)
                    })
                
                continue

            # Final Answer
            content = getattr(response, 'content', "") or "Done."
            return content

        return "Thinking loop limit reached."

    def _build_system_context(self):
        """Assemble system prompt with memory and skills."""
        context = []
        
        # 1. Base System Prompt
        context.append(self.system_prompt)

        # 2. Workspace Markdown Files (Soul, User, etc.)
        for file_name in ["SOUL.md", "USER.md", "TOOLS.md", "AGENTS.md", "HEARTBEAT.md"]:
            file_path = WORKSPACE_DIR / file_name
            if file_path.exists():
                try:
                    content = file_path.read_text(encoding="utf-8").strip()
                    if content:
                        context.append(f"\n{content}\n")
                except Exception as e:
                    print(f"[Agent] Error reading {file_name}: {e}")
        
        # 3. Memory Context
        mem = self.memory.get_memory_context()
        if mem:
            context.append(mem)
            
        # 4. Skills Context
        skills = self.skills.get_skill_prompts()
        if skills:
            context.append(skills)
            
        return "\n\n".join(context)
