import asyncio
import datetime
from typing import List, Dict

from .bus import InboundMessage

try:
    from croniter import croniter
except ImportError:
    croniter = None
    print("[Cron] croniter not installed. Basic interval scheduling only.")

class CronService:
    def __init__(self, bus, config_tasks: List[Dict]):
        self.bus = bus
        self.tasks = config_tasks
        self.running = False
        self._last_run = {}  # {task_idx: timestamp}

    async def start(self):
        print("[Cron] Service started.")
        self.running = True
        while self.running:
            now = datetime.datetime.now()
            
            for i, task in enumerate(self.tasks):
                schedule = task.get("schedule", "* * * * *")
                command_name = task.get("command", "unknown")
                
                if self._should_run(i, schedule, now):
                    print(f"[Cron] Triggering task: {command_name} ({task.get('description', '')})")
                    await self.bus.publish_inbound(InboundMessage(
                        channel="cron",
                        chat_id="system",
                        content=f"Execute scheduled task: {command_name}. Description: {task.get('description')}",
                        metadata={"type": "scheduled_task"}
                    ))
                    self._last_run[i] = now

            # Sleep for a minute to avoid busy loop
            await asyncio.sleep(60)

    def _should_run(self, task_idx: int, schedule_str: str, current_dt: datetime.datetime) -> bool:
        """Return True if the cron expression matches the current minute."""
        if not croniter:
            return False
        # croniter.match() checks whether current_dt falls on the cron schedule
        return croniter.match(schedule_str, current_dt)

    def stop(self):
        self.running = False

