"""
The Agent Loop:
1. Wait for InboundMessage.
2. Build Context (simplified history here).
3. Call LLM.
4. If Tool Call -> Execute Tool -> Loop Back to LLM.
5. If Final Answer -> Publish OutboundMessage.
"""
import asyncio
import json
from bus import MessageBus, InboundMessage, OutboundMessage
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

    async def run(self):
        print("[Agent] Started thinking loop...")
        while True:
            # 1. Wait for message (Ingress)
            msg = await self.bus.consume_inbound()
            print(f"[Agent] Received from {msg.channel}: {msg.content}")

            # 2. Update Context
            self.history.append({"role": "user", "content": msg.content})
            self.memory.append_history("user", msg.content)

            # 3. Think & Act Loop
            final_response = await self._think_and_act()

            # 4. Respond (Egress)
            self.history.append({"role": "assistant", "content": final_response})
            self.memory.append_history("assistant", final_response)
            
            out_msg = OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=final_response
            )
            await self.bus.publish_outbound(out_msg)

    async def _think_and_act(self):
        """
        The inner loop: LLM -> Tool -> LLM ... -> Final Answer
        """
        for _ in range(5):  # Max iterations
            # Construct messages with Memory and Skills context
            messages = [{"role": "system", "content": self._build_system_context()}]
            messages.extend(self.history[-10:])

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
                    result = await self.tools.execute(func_name, args)

                    # Add result to history
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": func_name,
                        "content": str(result)
                    })
                
                continue

            # Final Answer
            return getattr(response, 'content', "") or "Done."
        
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
