import asyncio
import sys

from ..bus import InboundMessage, OutboundMessage
from .base import BaseChannel


class CLIChannel(BaseChannel):
    """Terminal stdin/stdout channel."""

    async def start(self):
        print("[CLI] Channel started. Type your message...")
        loop = asyncio.get_event_loop()
        while True:
            content = await loop.run_in_executor(None, sys.stdin.readline)
            content = content.strip()
            if not content:
                continue
            await self.bus.publish_inbound(
                InboundMessage(channel="cli", chat_id="user1", content=content)
            )

    async def send(self, msg: OutboundMessage):
        print(f"\n[CLI] > Assistant: {msg.content}\n")
