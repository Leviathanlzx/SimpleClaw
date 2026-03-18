from abc import ABC, abstractmethod
from ..bus import MessageBus, OutboundMessage


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
