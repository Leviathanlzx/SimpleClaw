from datetime import datetime
import asyncio
import os
import subprocess
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
        try:
            if asyncio.iscoroutinefunction(func):
                return str(await func(**args))
            return str(func(**args))
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

