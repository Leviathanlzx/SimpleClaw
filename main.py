import asyncio
import os
from bus import MessageBus
from channels import CLIChannel
from provider import OpenAIProvider, MockProvider
from tools import setup_tools
from agent import AgentLoop
from config import config, WORKSPACE_DIR
from memory import MemoryStore
from skills import SkillsLoader
from cron import CronService

async def main():
    print("=== Nanobot Light Architecture Demo ===")
    
    # 0. Initialize Infrastructure
    bus = MessageBus()
    
    # 1. Load Resources
    memory = MemoryStore(WORKSPACE_DIR)
    skills = SkillsLoader(WORKSPACE_DIR)
    skills.discover_skills()
    
    # 2. Setup Provider
    # Check config for keys, fallback to mock if missing
    api_key = config.get("llm.api_key")
    if api_key and api_key != "YOUR_OPENROUTER_KEY":
        print(f"[Main] Using OpenRouter Provider...")
        provider = OpenAIProvider(
            api_key=api_key,
            base_url=config.get("llm.base_url"),
            model=config.get("llm.model")
        )
    else:
        print(f"[Main] API Key missing/default. Using MockProvider.")
        provider = MockProvider()

    # 3. Setup Tools & Agent
    tools = setup_tools(memory)
    agent = AgentLoop(bus, provider, tools, memory, skills)

    # 4. Senses (Channels)
    cli_channel = CLIChannel(bus)

    # 5. Cron Service
    cron_tasks = config.get("cron.tasks", [])
    cron_service = CronService(bus, cron_tasks)

    # 6. Start Services
    await asyncio.gather(
        agent.run(),                        # Logic Consumer
        cli_channel.start(),                # Input Producer
        _channel_dispatcher(bus, cli_channel), # Output Consumer
        cron_service.start()                # Timer Producer
    )

async def _channel_dispatcher(bus, cli_channel):
    """
    Simplified ChannelManager logic for this demo.
    Routes messages from Bus -> CliChannel.
    """
    while True:
        msg = await bus.consume_outbound()
        if msg.channel == "cli":
            await cli_channel.send(msg)
        elif msg.channel == "cron":
             # Cron messages are usually just triggers, they don't have an outbound destination
             pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nGoodbye!")

