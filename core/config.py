import json
import os
import shutil
from pathlib import Path
from typing import Any, List, Dict
from dataclasses import dataclass, field

@dataclass
class LLMConfig:
    provider: str = "openrouter"
    api_key: str = "YOUR_OPENROUTER_KEY"
    model: str = "openai/gpt-3.5-turbo"
    base_url: str = "https://openrouter.ai/api/v1"

@dataclass
class AgentConfig:
    name: str = "SimpleClaw"
    system_prompt: str = "You are a helpful AI assistant."
    max_loops: int = 10

@dataclass
class CronTaskConfig:
    schedule: str = "0 8 * * *"
    command: str = "say_good_morning"
    description: str = "Say good morning at 8am"

@dataclass
class CronConfig:
    tasks: List[Dict[str, str]] = field(default_factory=lambda: [
        {"schedule": "0 8 * * *", "command": "say_good_morning", "description": "Say good morning at 8am"}
    ])

@dataclass
class HeartbeatConfig:
    enabled: bool = True
    interval_s: int = 1800  # 30 minutes

@dataclass
class TelegramConfig:
    enabled: bool = False
    token: str = ""
    allowed_user_ids: List[int] = field(default_factory=list)  # 空列表 = 接受所有用户

@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    cron: CronConfig = field(default_factory=CronConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)

# Detect project root (one level up from core/config.py)
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
CONFIG_DIR = PROJECT_ROOT / "configs"
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
CONFIG_FILE = CONFIG_DIR / "config.json"
SIMPLECLAW_SKILLS_DIR = PROJECT_ROOT / "skills"

DEFAULT_CONFIG = {
    "llm": {
        "provider": "openrouter",
        "api_key": "YOUR_OPENROUTER_KEY",
        "model": "openai/gpt-3.5-turbo",
        "base_url": "https://openrouter.ai/api/v1"
    },
    "agent": {
        "name": "SimpleClaw",
        "system_prompt": "You are a helpful AI assistant.",
        "max_loops": 10
    },
    "cron": {
        "tasks": [
            {"schedule": "0 8 * * *", "command": "say_good_morning", "description": "Say good morning at 8am"}
        ]
    },
    "heartbeat": {
        "enabled": True,
        "interval_s": 1800
    },
    "telegram": {
        "enabled": False,
        "token": "",
        "allowed_user_ids": []
    }
}

DEFAULT_MD_FILES = {
    "SOUL.md": "# Identity & Soul\nYou are SimpleClaw, a highly capable AI assistant tailored for efficiency and precision.\nYour core personality is helpful, direct, and slightly witty.\n",
    "USER.md": "# User Context\nUser Name: Commander\nPreferences: Likes concise answers with code examples.\n",
    "TOOLS.md": "# Tools Strategy\n- Use tools whenever you need to retrieve external information.\n- If a tool fails, try to analyze the error before giving up.\n",
    "AGENTS.md": "# Sub-Agents Registry\n- Plan: Specialized in creating multi-step plans.\n",
    "HEARTBEAT.md": (
        "# Heartbeat Tasks\n\n"
        "This file is checked periodically by SimpleClaw.\n"
        "Add tasks below that you want the agent to work on in the background.\n\n"
        "If this file has no active tasks, the agent will skip the heartbeat.\n\n"
        "## Active Tasks\n\n"
        "<!-- Add your periodic tasks below this line -->\n\n\n"
        "## Completed\n\n"
        "<!-- Move completed tasks here or delete them -->\n"
    )
}

class ConfigLoader:
    def __init__(self):
        self.ensure_paths()
        self.config = self.load()

    def ensure_paths(self):
        """Ensure configs and workspace directories exist."""
        # 1. Configs structure
        if not CONFIG_DIR.exists():
            CONFIG_DIR.mkdir(parents=True)
            print(f"[Config] Created config directory: {CONFIG_DIR}")
        
        # Removed unused config subdirs (cron, history) to keep structure clean for Lite version

        # 2. Workspace structure
        if not WORKSPACE_DIR.exists():
            WORKSPACE_DIR.mkdir(parents=True)
            print(f"[Config] Created workspace directory: {WORKSPACE_DIR}")
        
        # Workspace subdirs
        (WORKSPACE_DIR / "memory").mkdir(exist_ok=True)
        (WORKSPACE_DIR / "history").mkdir(exist_ok=True)
        (WORKSPACE_DIR / "skills").mkdir(exist_ok=True)

        # 2.5 Apply Template (New)
        TEMPLATE_DIR = PROJECT_ROOT / "template"
        if TEMPLATE_DIR.exists():
            print(f"[Config] Applying template from {TEMPLATE_DIR}...")
            for item in TEMPLATE_DIR.iterdir():
                dest = WORKSPACE_DIR / item.name
                try:
                    if item.is_dir():
                        # If directory exists (like skills), merge contents
                        if not dest.exists():
                            shutil.copytree(item, dest)
                            print(f"[Config] Copied template directory: {item.name}")
                        else:
                            # Merge: copy missing items from template subdir
                            for subitem in item.iterdir():
                                subdest = dest / subitem.name
                                if not subdest.exists():
                                    if subitem.is_dir():
                                        shutil.copytree(subitem, subdest)
                                    else:
                                        shutil.copy2(subitem, subdest)
                                    print(f"[Config] Merged template item: {item.name}/{subitem.name}")
                    else:
                        # File
                        if not dest.exists():
                            shutil.copy2(item, dest)
                            print(f"[Config] Copied template file: {item.name}")
                except Exception as e:
                    print(f"[Config] Error copying template item {item.name}: {e}")

        # 3. Import Skills
        # Copy builtin skills from simpleclaw/skills if available
        if SIMPLECLAW_SKILLS_DIR.exists():
            print(f"[Config] Importing builtin skills from {SIMPLECLAW_SKILLS_DIR}...")
            
            # Copy skills root README.md (Overview)
            readme_path = SIMPLECLAW_SKILLS_DIR / "README.md"
            dest_readme = WORKSPACE_DIR / "skills" / "README.md"
            if readme_path.exists():
                if not dest_readme.exists():
                    shutil.copy2(readme_path, dest_readme)
                    print("[Config] Imported skills overview: README.md")
            
            for skill_path in SIMPLECLAW_SKILLS_DIR.iterdir():
                if skill_path.is_dir():
                    skill_name = skill_path.name
                    source_skill_file = skill_path / "SKILL.md"
                    
                    if source_skill_file.exists():
                        dest_skill_dir = WORKSPACE_DIR / "skills" / skill_name
                        dest_skill_file = dest_skill_dir / "SKILL.md"
                        
                        if not dest_skill_dir.exists():
                            dest_skill_dir.mkdir(parents=True)
                            shutil.copy2(source_skill_file, dest_skill_file)
                            
                            # Copy README.md as well if exists
                            source_readme_file = skill_path / "README.md"
                            if source_readme_file.exists():
                                dest_readme_file = dest_skill_dir / "README.md"
                                shutil.copy2(source_readme_file, dest_readme_file)
                                
                            print(f"[Config] Imported skill: {skill_name}")
                        else:
                            # If file missing but dir exists
                            if not dest_skill_file.exists():
                                shutil.copy2(source_skill_file, dest_skill_file)
                                print(f"[Config] Restored missing skill file: {skill_name}")
                            
                            # Ensure README.md is present if source has it
                            source_readme_file = skill_path / "README.md"
                            if source_readme_file.exists():
                                dest_readme_file = dest_skill_dir / "README.md"
                                if not dest_readme_file.exists():
                                    shutil.copy2(source_readme_file, dest_readme_file)
                                    print(f"[Config] Restored missing readme file: {skill_name}")

        # 4. Create standard markdown context files
        for filename, content in DEFAULT_MD_FILES.items():
            file_path = WORKSPACE_DIR / filename
            if not file_path.exists():
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"[Config] Created default context file: {filename}")

        # 5. Create Config File
        if not CONFIG_FILE.exists():
            print(f"[Config] Config file not found. Creating default at: {CONFIG_FILE}")
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4)
        
        print(f"[Config] Initialization complete.")

    def load(self) -> AppConfig:
        """Load configuration from JSON file and parse into Dataclasses."""
        data = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                print(f"[Config] Loaded configuration from {CONFIG_FILE}")
            except Exception as e:
                print(f"[Config] Error loading config: {e}")
                data = DEFAULT_CONFIG
        else:
            data = DEFAULT_CONFIG
            
        # Parse into objects safely
        llm_data = data.get("llm", {})
        agent_data = data.get("agent", {})
        cron_data = data.get("cron", {})
        heartbeat_data = data.get("heartbeat", {})
        telegram_data = data.get("telegram", {})

        # Filter valid keys for dataclasses
        llm_kwargs = {k: v for k, v in llm_data.items() if hasattr(LLMConfig, k)}
        agent_kwargs = {k: v for k, v in agent_data.items() if hasattr(AgentConfig, k)}
        heartbeat_kwargs = {k: v for k, v in heartbeat_data.items() if hasattr(HeartbeatConfig, k)}
        telegram_kwargs = {k: v for k, v in telegram_data.items() if hasattr(TelegramConfig, k)}

        return AppConfig(
            llm=LLMConfig(**llm_kwargs),
            agent=AgentConfig(**agent_kwargs),
            cron=CronConfig(tasks=cron_data.get("tasks", [])),
            heartbeat=HeartbeatConfig(**heartbeat_kwargs),
            telegram=TelegramConfig(**telegram_kwargs),
        )

# Global instance for easy access
config = ConfigLoader().config

