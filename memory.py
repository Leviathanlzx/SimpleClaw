import os
import datetime
from pathlib import Path
from typing import List, Dict, Any

class MemoryStore:
    """Manages long-term (MEMORY.md) and short-term (interaction history) memory."""
    def __init__(self, workspace: Path):
        self.memory_dir = workspace / "memory"
        self.history_dir = workspace / "history"
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.history_dir / "HISTORY.md"
        self.full_history_file = self.history_dir / "FULL_HISTORY.md"
        self._ensure_paths()
        
    def _ensure_paths(self):
        """Create directories and files if they do not exist."""
        if not self.memory_dir.exists():
            self.memory_dir.mkdir(parents=True)
            print(f"[Memory] Created memory directory: {self.memory_dir}")
            
        if not self.history_dir.exists():
            self.history_dir.mkdir(parents=True)
            print(f"[Memory] Created history directory: {self.history_dir}")

        # Check for legacy HISTORY.md in the parent memory folder and migrate it
        # Check both old locations: memory/HISTORY.md and memory/history/HISTORY.md
        old_history_file = self.memory_dir / "HISTORY.md"
        old_history_subdir_file = self.memory_dir / "history" / "HISTORY.md"
        
        migration_source = None
        if old_history_subdir_file.exists():
            migration_source = old_history_subdir_file
            # If we migrate from subdir, we might want to clean up the empty dir
        elif old_history_file.exists():
            migration_source = old_history_file

        if migration_source and not self.history_file.exists():
            try:
                print(f"[Memory] Migrating legacy usage data from {migration_source} to {self.history_file}...")
                os.rename(migration_source, self.history_file)
                
                # Cleanup empty history subdir if it was the source
                if migration_source == old_history_subdir_file:
                     try:
                        migration_source.parent.rmdir()
                     except:
                        pass
            except Exception as e:
                print(f"[Memory] Error migrating history file: {e}")

        self._touch_file(self.memory_file, "# Long-Term Memory\n\n- No detailed facts stored yet.\n")
        self._touch_file(self.history_file, "# Conversation History\n\n")
        self._touch_file(self.full_history_file, "# Full Agent Interaction Log\n\n")

    def _touch_file(self, filepath: Path, default_content=""):
        if not filepath.exists():
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(default_content)

    def load_long_term(self) -> str:
        """Read the content of MEMORY.md."""
        try:
            return self.memory_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def update_long_term(self, content: str):
        """Overwrite MEMORY.md with new consolidated facts."""
        with open(self.memory_file, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[Memory] Updated long-term memory")

    def append_history(self, role: str, content: str):
        """Log an interaction to HISTORY.md."""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"**[{timestamp}] {role.title()}:**\n{content}\n\n---\n\n"
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry)
        print(f"[Memory] Logged to history: {role}")

    def append_full_log(self, title: str, data: Any, format_type: str = "json"):
        """Log data to FULL_HISTORY.md for debugging/audit."""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        
        content = ""
        if format_type == "json":
            # Try to pretty print JSON/Dicts
            if isinstance(data, (dict, list)):
                try:
                    import json
                    content = json.dumps(data, indent=2, default=str)
                except:
                    content = str(data)
            else:
                content = str(data)
            entry = f"\n## [{timestamp}] {title}\n```json\n{content}\n```\n"

        elif format_type == "markdown":
            content = str(data)
            entry = f"\n## [{timestamp}] {title}\n\n{content}\n"
            
        else:
             content = str(data)
             entry = f"\n## [{timestamp}] {title}\n\n{content}\n"
        
        try:
            with open(self.full_history_file, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            print(f"[Memory] Error logging to full history: {e}")

    def get_memory_context(self) -> str:
        """Compose a prompt section with memory."""
        long_term = self.load_long_term()
        # Optionally could add recent history here if needed
        return f"\n{long_term}\n"
