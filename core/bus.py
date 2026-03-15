import asyncio
from dataclasses import dataclass, field
from typing import Any

@dataclass
class InboundMessage:
    """Standardized message from a Channel to the Agent."""
    channel: str
    chat_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class OutboundMessage:
    """Standardized message from the Agent to a Channel."""
    channel: str
    chat_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

class MessageBus:
    """
    Decouples IO (Channels) from Logic (Agent).
    Channels push to Inbound. Agent pushes to Outbound.
    """
    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, msg: InboundMessage):
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage):
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        return await self.outbound.get()

