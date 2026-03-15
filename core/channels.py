import asyncio
import sys
from abc import ABC, abstractmethod
from .bus import MessageBus, InboundMessage, OutboundMessage

class BaseChannel(ABC):
    def __init__(self, bus: MessageBus):
        self.bus = bus

    @abstractmethod
    async def start(self):
        """Start listening for input."""
        ...

    @abstractmethod
    async def send(self, msg: OutboundMessage):
        """Send output to platform."""
        ...

class CLIChannel(BaseChannel):
    """
    Simulates a chat interface using the terminal stdin/stdout.
    """
    async def start(self):
        print("[CLI] Channel started. Type 'hello' or 'time'...")
        loop = asyncio.get_event_loop()
        while True:
            # Non-blocking input handling
            content = await loop.run_in_executor(None, sys.stdin.readline)
            content = content.strip()
            if not content: continue
            
            # Pack into standard format
            msg = InboundMessage(
                channel="cli",
                chat_id="user1",
                content=content
            )
            # Push to Bus
            await self.bus.publish_inbound(msg)

    async def send(self, msg: OutboundMessage):
        # Unpack from standard format
        print(f"\n[CLI] > Assisant: {msg.content}\n")

class ChannelManager:
    """
    Manages multiple channels and routes outbound messages.
    """
    def __init__(self, bus: MessageBus):
        self.bus = bus
        self.channels = {}

    def register(self, name, channel):
        self.channels[name] = channel

    async def start_all(self):
        # 1. Start all channels (Input listeners)
        tasks = [channel.start() for channel in self.channels.values()]
        
        # 2. Start Dispatcher (Output router)
        tasks.append(self._dispatch_outbound())
        
        return await asyncio.gather(*tasks)

    async def _dispatch_outbound(self):
        """
        Route messages from Agent -> Specific Channel
        """
        while True:
            msg = await self.bus.consume_outbound()
            if channel := self.channels.get(msg.channel):
                await channel.send(msg)
            else:
                print(f"[Manager] Error: Channel {msg.channel} not found")


