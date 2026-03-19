import asyncio
import datetime
import json
import uuid
from typing import List, Dict

from .bus import InboundMessage
from .config import CONFIG_FILE

try:
    from croniter import croniter
except ImportError:
    croniter = None
    print("[Cron] croniter not installed. Cron expression scheduling unavailable.")


class CronService:
    def __init__(self, bus, config_tasks: List[Dict]):
        self.bus = bus
        self._config_tasks = config_tasks       # static tasks from config.json
        self._dynamic_tasks: Dict[str, Dict] = {}  # id -> task dict
        self._last_run: Dict[str, datetime.datetime] = {}
        self.running = False
        self._load_dynamic_tasks()

    # ------------------------------------------------------------------
    # Public API (called by agent via cron tool)
    # ------------------------------------------------------------------

    def add_task(
        self,
        message: str,
        every_seconds: int = None,
        cron_expr: str = None,
        at: str = None,
    ) -> str:
        """Schedule a new dynamic task. Returns the task ID."""
        if not any([every_seconds, cron_expr, at]):
            raise ValueError("Must specify one of: every_seconds, cron_expr, at")

        task_id = str(uuid.uuid4())[:8]

        if at:
            task = {
                "id": task_id,
                "message": message,
                "type": "once",
                "at": datetime.datetime.fromisoformat(at),
                "done": False,
            }
        elif every_seconds:
            task = {
                "id": task_id,
                "message": message,
                "type": "interval",
                "interval_seconds": int(every_seconds),
            }
        else:
            if not croniter:
                raise ValueError("croniter is not installed; cron_expr scheduling unavailable.")
            task = {
                "id": task_id,
                "message": message,
                "type": "cron",
                "cron_expr": cron_expr,
            }

        self._dynamic_tasks[task_id] = task
        self._persist_dynamic_tasks()
        print(f"[Cron] Added dynamic task {task_id}: {message[:60]}")
        return task_id

    def remove_task(self, task_id: str) -> bool:
        """Remove a dynamic task by ID. Returns True if found and removed."""
        if task_id in self._dynamic_tasks:
            del self._dynamic_tasks[task_id]
            self._last_run.pop(task_id, None)
            self._persist_dynamic_tasks()
            print(f"[Cron] Removed task {task_id}")
            return True
        return False

    def list_tasks(self) -> List[Dict]:
        """Return all scheduled tasks (static + dynamic)."""
        result = []

        for i, t in enumerate(self._config_tasks):
            result.append({
                "id": f"config_{i}",
                "type": "config",
                "schedule": t.get("schedule", ""),
                "description": t.get("description", t.get("command", "")),
            })

        for task in self._dynamic_tasks.values():
            info = {"id": task["id"], "type": task["type"], "message": task["message"]}
            if task["type"] == "interval":
                info["every_seconds"] = task["interval_seconds"]
            elif task["type"] == "cron":
                info["cron_expr"] = task["cron_expr"]
            elif task["type"] == "once":
                info["at"] = task["at"].isoformat()
            result.append(info)

        return result

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def start(self):
        print("[Cron] Service started.")
        self.running = True
        while self.running:
            now = datetime.datetime.now()

            # Static config tasks (cron-expression based)
            for i, task in enumerate(self._config_tasks):
                task_id = f"config_{i}"
                schedule = task.get("schedule", "* * * * *")
                if self._should_run_cron(task_id, schedule, now):
                    command_name = task.get("command", "unknown")
                    print(f"[Cron] Triggering config task: {command_name} ({task.get('description', '')})")
                    await self.bus.publish_inbound(InboundMessage(
                        channel="cron",
                        chat_id="system",
                        content=f"Execute scheduled task: {command_name}. Description: {task.get('description')}",
                        metadata={"type": "scheduled_task"},
                    ))
                    self._last_run[task_id] = now

            # Dynamic tasks
            to_remove = []
            for task_id, task in list(self._dynamic_tasks.items()):
                should_fire = False

                if task["type"] == "interval":
                    last = self._last_run.get(task_id)
                    if last is None or (now - last).total_seconds() >= task["interval_seconds"]:
                        should_fire = True

                elif task["type"] == "cron":
                    should_fire = self._should_run_cron(task_id, task["cron_expr"], now)

                elif task["type"] == "once":
                    if not task.get("done") and now >= task["at"]:
                        should_fire = True
                        task["done"] = True
                        to_remove.append(task_id)

                if should_fire:
                    print(f"[Cron] Triggering dynamic task {task_id}: {task['message'][:60]}")
                    await self.bus.publish_inbound(InboundMessage(
                        channel="cron",
                        chat_id="system",
                        content=task["message"],
                        metadata={"type": "scheduled_task", "task_id": task_id},
                    ))
                    self._last_run[task_id] = now

            if to_remove:
                for task_id in to_remove:
                    self._dynamic_tasks.pop(task_id, None)
                    print(f"[Cron] One-time task {task_id} completed and removed.")
                self._persist_dynamic_tasks()

            await asyncio.sleep(30)  # check every 30 seconds

    def _should_run_cron(self, task_id: str, schedule_str: str, current_dt: datetime.datetime) -> bool:
        """Return True if the cron expression matches current minute, and hasn't fired this minute."""
        if not croniter:
            return False
        last = self._last_run.get(task_id)
        if last and last.replace(second=0, microsecond=0) == current_dt.replace(second=0, microsecond=0):
            return False
        return croniter.match(schedule_str, current_dt)

    def _task_to_dict(self, task: Dict) -> Dict:
        """Serialize a task to a JSON-safe dict."""
        d = {k: v for k, v in task.items() if k != "at"}
        if "at" in task:
            at = task["at"]
            d["at"] = at.isoformat() if isinstance(at, datetime.datetime) else at
        return d

    def _load_dynamic_tasks(self):
        """Load persisted dynamic tasks from config.json on startup."""
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            saved = data.get("cron", {}).get("dynamic_tasks", [])
            for t in saved:
                if t.get("type") == "once":
                    at = datetime.datetime.fromisoformat(t["at"])
                    if at <= datetime.datetime.now():
                        continue  # already past, skip
                    t = {**t, "at": at, "done": False}
                self._dynamic_tasks[t["id"]] = t
            if self._dynamic_tasks:
                print(f"[Cron] Restored {len(self._dynamic_tasks)} dynamic task(s) from config.")
        except Exception as e:
            print(f"[Cron] Could not load dynamic tasks: {e}")

    def _persist_dynamic_tasks(self):
        """Write current dynamic tasks back to config.json."""
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            data.setdefault("cron", {})["dynamic_tasks"] = [
                self._task_to_dict(t) for t in self._dynamic_tasks.values()
            ]
            CONFIG_FILE.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[Cron] Failed to persist dynamic tasks: {e}")

    def stop(self):
        self.running = False
