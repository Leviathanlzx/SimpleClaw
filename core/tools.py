from datetime import datetime
import asyncio
import os
import subprocess
import inspect
from pathlib import Path
from .memory import MemoryStore
from .config import WORKSPACE_DIR

class ToolRegistry:
    """
    Registry for functions that the Agent can execute.
    """
    def __init__(self, memory: MemoryStore = None):
        self._tools = {}
        self.memory = memory
        # Channel/chat context — set by AgentLoop before each message processing.
        # Tools that need routing info (e.g. cron) read from here.
        self._context_channel: str = "cli"
        self._context_chat_id: str = "user1"

    def set_context(self, channel: str, chat_id: str):
        """Update the current channel/chat context. Called by AgentLoop each turn."""
        self._context_channel = channel or "cli"
        self._context_chat_id = chat_id or "user1"

    def register(self, name, func, description, parameters=None):
        self._tools[name] = {"func": func, "description": description, "parameters": parameters}

    def get_definitions(self) -> list[dict]:
        definitions = []
        for name, data in self._tools.items():
            tool_def = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": data["description"],
                    "parameters": data["parameters"] or {"type": "object", "properties": {}},
                }
            }
            definitions.append(tool_def)
        return definitions

    async def execute(self, name: str, args: dict) -> str:
        """Execute a registered tool by name with the given arguments."""
        if name not in self._tools:
            return f"Error: Tool '{name}' not found"
        func = self._tools[name]["func"]

        # Filter kwargs to only what the function accepts and exclude unexpected keys
        sig = inspect.signature(func)
        valid_params = set(sig.parameters.keys())
        has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

        if has_kwargs:
            filtered_args = args
        else:
            filtered_args = {k: v for k, v in args.items() if k in valid_params}

        try:
            if asyncio.iscoroutinefunction(func):
                return str(await func(**filtered_args))
            return str(func(**filtered_args))
        except Exception as e:
            return f"Error executing {name}: {e}"

# --- Tools ---

def get_time():
    return datetime.now().isoformat()

async def exec_shell(command: str):
    """Execute a shell command."""
    try:
        # Force UTF-8 on Windows by setting code page 65001 and env vars
        env = os.environ.copy()
        if os.name == "nt":
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            actual_command = f"chcp 65001 > nul 2>&1 & {command}"
        else:
            actual_command = command

        # Use asyncio subprocess for non-blocking execution
        process = await asyncio.create_subprocess_shell(
            actual_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKSPACE_DIR),  # Default to workspace for safety
            env=env,
        )
        stdout, stderr = await process.communicate()

        output = stdout.decode("utf-8", errors="replace").strip()
        error = stderr.decode("utf-8", errors="replace").strip()
        
        if error:
            return f"Stdout:\n{output}\n\nStderr:\n{error}"
        return output if output else "(No output)"
    except Exception as e:
        return f"Error executing shell command: {e}"

def read_file(path: str):
    """Read a file from the workspace."""
    try:
        # Security: Resolving path against workspace
        safe_path = (WORKSPACE_DIR / path).resolve()
        if not str(safe_path).startswith(str(WORKSPACE_DIR.resolve())):
           return "Error: Access denied (outside workspace)."
           
        if not safe_path.exists():
            return "Error: File not found."
            
        return safe_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"

def write_file(path: str, content: str):
    """Write content to a file in the workspace."""
    try:
        safe_path = (WORKSPACE_DIR / path).resolve()
        if not str(safe_path).startswith(str(WORKSPACE_DIR.resolve())):
           return "Error: Access denied (outside workspace)."
           
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_text(content, encoding="utf-8")
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error writing file: {e}"

def list_dir(path: str = "."):
    """List files in a workspace directory."""
    try:
        safe_path = (WORKSPACE_DIR / path).resolve()
        if not str(safe_path).startswith(str(WORKSPACE_DIR.resolve())):
           return "Error: Access denied (outside workspace)."
           
        if not safe_path.exists():
            return "Error: Directory not found."
            
        items = []
        for item in safe_path.iterdir():
            type_char = "d" if item.is_dir() else "f"
            items.append(f"[{type_char}] {item.name}")
        return "\n".join(items) if items else "(Empty directory)"
    except Exception as e:
        return f"Error listing directory: {e}"

def register_cron_tool(registry: ToolRegistry, cron_service) -> None:
    """Register the cron tool onto an existing ToolRegistry using a CronService instance."""
    import json as _json

    def cron_tool(
        action: str,
        message: str = None,
        every_seconds: int = None,
        cron_expr: str = None,
        at: str = None,
        job_id: str = None,
    ) -> str:
        if action == "add":
            if not message:
                return "Error: message is required for add action"
            try:
                # Auto-capture current channel context — no LLM guessing needed
                task_id = cron_service.add_task(
                    message=message,
                    every_seconds=every_seconds,
                    cron_expr=cron_expr,
                    at=at,
                    target_channel=registry._context_channel,
                    target_chat_id=registry._context_chat_id,
                )
                return (
                    f"Task scheduled successfully. job_id: {task_id} "
                    f"(will deliver to {registry._context_channel}:{registry._context_chat_id})"
                )
            except ValueError as e:
                return f"Error: {e}"

        elif action == "list":
            tasks = cron_service.list_tasks()
            if not tasks:
                return "No scheduled tasks."
            return _json.dumps(tasks, default=str, indent=2)

        elif action == "remove":
            if not job_id:
                return "Error: job_id is required for remove action"
            if cron_service.remove_task(job_id):
                return f"Task {job_id} removed."
            return f"Task {job_id} not found."

        else:
            return f"Unknown action: {action}. Use: add, list, remove"

    registry.register(
        "cron",
        cron_tool,
        "Schedule tasks or reminders to run at a specific time or on a recurring schedule. "
        "Actions: 'add' (schedule a new task), 'list' (show all tasks), 'remove' (cancel a task). "
        "For 'add': provide message + exactly one of: every_seconds (interval in seconds), "
        "cron_expr (5-field cron expression e.g. '0 9 * * *'), or at (ISO datetime for one-time task). "
        "The target channel is automatically captured from the current conversation context.",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "remove"],
                    "description": "Action: 'add' to schedule, 'list' to show all, 'remove' to cancel",
                },
                "message": {
                    "type": "string",
                    "description": "The task or reminder message (required for add)",
                },
                "every_seconds": {
                    "type": "integer",
                    "minimum": 60,
                    "description": "Repeat interval in seconds, minimum 60 (e.g. 3600 = hourly, 86400 = daily). Use cron_expr instead for clock-aligned schedules like 'every day at 9am'.",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "5-field cron expression (e.g. '0 9 * * *' = 9am daily, '0 9 * * 1-5' = weekdays)",
                },
                "at": {
                    "type": "string",
                    "description": "ISO datetime for a one-time task (e.g. '2024-06-01T09:00:00')",
                },
                "job_id": {
                    "type": "string",
                    "description": "Task ID returned by add (required for remove)",
                },
            },
            "required": ["action"],
        },
    )


def setup_tools(memory: MemoryStore = None) -> ToolRegistry:
    registry = ToolRegistry(memory)
    
    registry.register("get_time", get_time, "Get current time")
    
    # Register Core Tools
    registry.register(
        "exec", 
        exec_shell, 
        "Execute a shell command in the workspace. Use for running scripts, installing packages, etc.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run (e.g. 'ls -la', 'python script.py')"},
            },
            "required": ["command"],
        },
    )
    
    registry.register(
        "read_file", 
        read_file, 
        "Read file content.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to file in workspace"},
            },
            "required": ["path"],
        },
    )
    
    registry.register(
        "write_file", 
        write_file, 
        "Write content to a file.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to file in workspace"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
    )
    
    registry.register(
        "list_dir", 
        list_dir, 
        "List files in a directory.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path in workspace (default: .)"},
            },
            "required": [],
        },
    )

    if memory:
        def _save_memory_impl(content: str):
            memory.update_long_term(content)
            return "Successfully updated long-term memory."
        
        registry.register(
            "save_memory", 
            _save_memory_impl, 
            "Update long-term memory with consolidated facts. COMPLETELY OVERWRITES existing memory. "
            "IMPORTANT: Content MUST adhere to the strict Markdown structure (User Information, Preferences, Project Context, Important Notes).",
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The full content of the memory file with all required headers."},
                },
                "required": ["content"],
            },
        )
        
    return registry

