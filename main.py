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

from core.bus import MessageBus, OutboundMessage
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

    # 5. Cron Service (callback wired after agent creation)
    cron_tasks = config.cron.tasks
    cron_service = CronService(config_tasks=cron_tasks)
    register_cron_tool(tools, cron_service)

    agent = AgentLoop(bus, provider, tools, memory, skills)

    # 6. Wire cron callback: task fires → agent.process_direct → publish outbound
    async def on_cron_job(task: dict) -> str | None:
        """Execute a cron task through the agent with an independent session."""
        task_id = task["id"]
        target_ch = task.get("target_channel", "cli")
        target_cid = task.get("target_chat_id", "user1")

        response = await agent.process_direct(
            content=f"[Scheduled Task] {task['message']}",
            session_key=f"cron:{task_id}",
            channel=target_ch,
            chat_id=target_cid,
        )

        if response and target_ch and target_cid:
            await bus.publish_outbound(OutboundMessage(
                channel=target_ch,
                chat_id=target_cid,
                content=response,
            ))
            print(f"[Cron] Response delivered to {target_ch}:{target_cid}")

        return response

    cron_service.set_on_job(on_cron_job)

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

    # 9. Heartbeat Service (with smart target selection + callback)
    enabled_channels = set()
    if telegram_channel:
        enabled_channels.add("telegram")
    if wecom_channel:
        enabled_channels.add("wecom")

    heartbeat_service = HeartbeatService(
        workspace=WORKSPACE_DIR,
        provider=provider,
        bus=bus,
        interval_s=config.heartbeat.interval_s,
        enabled=config.heartbeat.enabled,
        process_direct=agent.process_direct,
        list_sessions=agent.list_sessions,
        enabled_channels=enabled_channels,
    )

    # 10. Start Services
    services = [
        agent.run(),                           # Logic Consumer
        cli_channel.start(),                   # Input Producer
        _channel_dispatcher(bus, cli_channel, telegram_channel, wecom_channel),  # Output Consumer
        cron_service.start(),                  # Timer Producer (uses callback, not bus)
        heartbeat_service.start(),             # Heartbeat Producer (uses callback, not bus)
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
        else:
            # Fallback: log unrouted messages to CLI
            print(f"\n[{msg.channel}] > {msg.content}\n")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nGoodbye!")
