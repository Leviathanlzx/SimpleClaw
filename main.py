import asyncio
import os
import sys

# Force UTF-8 globally on Windows before anything else loads
if os.name == "nt":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr.encoding != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from core.bus import MessageBus
from core.channels import CLIChannel, TelegramChannel, WecomChannel
from core.provider import OpenAIProvider, MockProvider
from core.tools import setup_tools, register_cron_tool
from core.agent import AgentLoop
from core.config import config, WORKSPACE_DIR
from core.memory import MemoryStore
from core.skills import SkillsLoader
from core.cron import CronService
from core.heartbeat import HeartbeatService

async def main():
    print("=== SimpleClaw ===")
    
    # 0. Initialize Infrastructure
    bus = MessageBus()
    
    # 1. Load Resources
    memory = MemoryStore(WORKSPACE_DIR)
    skills = SkillsLoader(WORKSPACE_DIR)
    skills.discover_skills()
    
    # 2. Setup Provider
    # Check config for keys, fallback to mock if missing
    api_key = config.llm.api_key
    if api_key and api_key != "YOUR_OPENROUTER_KEY":
        print(f"[Main] Using OpenRouter Provider...")
        provider = OpenAIProvider(
            api_key=api_key,
            base_url=config.llm.base_url,
            model=config.llm.model
        )
    else:
        print(f"[Main] API Key missing/default. Using MockProvider.")
        provider = MockProvider()

    # 3. Setup Tools & Agent
    tools = setup_tools(memory)

    # 4. Senses (Channels)
    cli_channel = CLIChannel(bus)

    # 5. Cron Service
    cron_tasks = config.cron.tasks
    cron_service = CronService(bus, cron_tasks)
    register_cron_tool(tools, cron_service)

    agent = AgentLoop(bus, provider, tools, memory, skills)

    # 6. Heartbeat Service
    heartbeat_service = HeartbeatService(
        workspace=WORKSPACE_DIR,
        provider=provider,
        bus=bus,
        interval_s=config.heartbeat.interval_s,
        enabled=config.heartbeat.enabled,
    )

    # 7. Telegram Channel (optional, enabled via config)
    tg_cfg = config.telegram
    telegram_channel = TelegramChannel(
        token=tg_cfg.token,
        bus=bus,
        allowed_user_ids=tg_cfg.allowed_user_ids,
    ) if tg_cfg.enabled else None

    if telegram_channel:
        print("[Main] Telegram channel enabled.")

    # 8. WeCom Channel (optional, enabled via config)
    wc_cfg = config.wecom
    wecom_channel = WecomChannel(
        bot_id=wc_cfg.bot_id,
        secret=wc_cfg.secret,
        bus=bus,
        allowed_user_ids=wc_cfg.allowed_user_ids,
        welcome_message=wc_cfg.welcome_message,
    ) if wc_cfg.enabled else None

    if wecom_channel:
        print("[Main] WeCom channel enabled.")

    # 9. Start Services
    services = [
        agent.run(),                           # Logic Consumer
        cli_channel.start(),                   # Input Producer
        _channel_dispatcher(bus, cli_channel, telegram_channel, wecom_channel),  # Output Consumer
        cron_service.start(),                  # Timer Producer
        heartbeat_service.start(),             # Heartbeat Producer
    ]
    if telegram_channel:
        services.append(telegram_channel.start())  # Telegram Inbound Producer
    if wecom_channel:
        services.append(wecom_channel.start())     # WeCom Inbound Producer

    await asyncio.gather(*services)

async def _channel_dispatcher(bus, cli_channel, telegram_channel=None, wecom_channel=None):
    """
    Routes OutboundMessages from Bus -> appropriate Channel.
    """
    while True:
        msg = await bus.consume_outbound()
        if msg.channel == "cli":
            await cli_channel.send(msg)
        elif msg.channel == "telegram":
            if telegram_channel:
                await telegram_channel.send(msg)
        elif msg.channel == "wecom":
            if wecom_channel:
                await wecom_channel.send(msg)
        elif msg.channel == "heartbeat":
            # Heartbeat responses are printed to CLI with a distinct prefix
            print(f"\n[Heartbeat] > Agent: {msg.content}\n")
        elif msg.channel == "cron":
            # Cron triggers don't require an outbound response
            pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nGoodbye!")

