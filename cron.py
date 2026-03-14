import asyncio
import time
from typing import Callable, List, Dict
import datetime

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
                    # Send an internal message to trigger the agent
                    # For simplicity, we inject it into the inbound queue as a system event
                    from bus import InboundMessage
                    await self.bus.publish_inbound(InboundMessage(
                        channel="cron",
                        chat_id="system",
                        content=f"Execute scheduled task: {command_name}. Description: {task.get('description')}",
                        metadata={"type": "scheduled_task"}
                    ))
                    self._last_run[i] = now

            # Sleep for a minute to avoid busy loop
            await asyncio.sleep(60)

    def _should_run(self, task_idx, schedule_str, current_dt):
        """Check if the task should run now based on cron string."""
        if not croniter:
            return False # Fallback logic not implemented for brevity
            
        # Proper cron check using croniter
        # For a simple demo, we just check if the last run + interval < now is too complex without state.
        # Efficient way: Calculate next run time from last run time.
        
        last_run = self._last_run.get(task_idx, current_dt - datetime.timedelta(minutes=1)) # defaulting to allowed
        iter = croniter(schedule_str, last_run)
        next_run = iter.get_next(datetime.datetime)
        
        # If next run time is in the past or now (within a minute tolerance), run it.
        # But since we check every minute, we just need to see if we crossed the threshold.
        # Actually croniter is better used to check if the current time matches the pattern.
        return croniter.match(schedule_str, current_dt)

    def stop(self):
        self.running = False

