import json
import shutil
from pathlib import Path
from typing import List, Dict
from dataclasses import dataclass, field


# ── Config dataclasses ─────────────────────────────────────────────────────────

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
    allowed_user_ids: List[int] = field(default_factory=list)  # empty = accept all users

@dataclass
class WecomConfig:
    enabled: bool = False
    bot_id: str = ""
    secret: str = ""
    allowed_user_ids: List[str] = field(default_factory=list)  # empty = accept all users
    welcome_message: str = ""

@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    cron: CronConfig = field(default_factory=CronConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    wecom: WecomConfig = field(default_factory=WecomConfig)


# ── Paths ──────────────────────────────────────────────────────────────────────

# Detect project root: one level up from core/config.py
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
CONFIG_DIR = PROJECT_ROOT / "configs"
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
CONFIG_TEMPLATE = CONFIG_DIR / "config.json"   # source template (bundled in image)
CONFIG_FILE = WORKSPACE_DIR / "config.json"    # runtime config (in mounted volume)
SIMPLECLAW_SKILLS_DIR = PROJECT_ROOT / "skills"


# ── Defaults ───────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "llm": {
        "provider": "openrouter",
        "api_key": "YOUR_OPENROUTER_KEY",
        "model": "openai/gpt-3.5-turbo",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "agent": {
        "name": "SimpleClaw",
        "system_prompt": "You are a helpful AI assistant.",
        "max_loops": 10,
    },
    "cron": {
        "tasks": [
            {"schedule": "0 8 * * *", "command": "say_good_morning", "description": "Say good morning at 8am"}
        ]
    },
    "heartbeat": {"enabled": True, "interval_s": 1800},
    "telegram": {"enabled": False, "token": "", "allowed_user_ids": []},
    "wecom": {"enabled": False, "bot_id": "", "secret": "", "allowed_user_ids": [], "welcome_message": ""},
}

# Default workspace context files loaded into the system prompt
DEFAULT_MD_FILES = {
    "SOUL.md": (
        "# Identity & Soul\n"
        "You are SimpleClaw, a highly capable AI assistant tailored for efficiency and precision.\n"
        "Your core personality is helpful, direct, and slightly witty.\n"
    ),
    "USER.md": (
        "# User Context\n"
        "User Name: Commander\n"
        "Preferences: Likes concise answers with code examples.\n"
    ),
    "TOOLS.md": (
        "# Tools Strategy\n"
        "- Use tools whenever you need to retrieve external information.\n"
        "- If a tool fails, try to analyze the error before giving up.\n"
    ),
    "AGENTS.md": (
        "# Sub-Agents Registry\n"
        "- Plan: Specialized in creating multi-step plans.\n"
    ),
    "HEARTBEAT.md": (
        "# Heartbeat Tasks\n\n"
        "This file is checked periodically by SimpleClaw.\n"
        "Add tasks below that you want the agent to work on in the background.\n\n"
        "If this file has no active tasks, the agent will skip the heartbeat.\n\n"
        "## Active Tasks\n\n"
        "<!-- Add your periodic tasks below this line -->\n\n\n"
        "## Completed\n\n"
        "<!-- Move completed tasks here or delete them -->\n"
    ),
}


# ── ConfigLoader ───────────────────────────────────────────────────────────────

class ConfigLoader:
    def __init__(self):
        self.ensure_paths()
        self.config = self.load()

    def ensure_paths(self):
        """Create all required directories, files, and resources on first run."""
        self._create_dirs()
        self._apply_template()
        self._import_builtin_skills()
        self._create_default_md_files()
        self._create_config_file()
        print("[Config] Initialization complete.")

    def _create_dirs(self):
        """Ensure all workspace subdirectories exist."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        for subdir in ("memory", "history", "skills"):
            (WORKSPACE_DIR / subdir).mkdir(exist_ok=True)

    def _apply_template(self):
        """Copy template files into workspace (only if the destination doesn't already exist)."""
        template_dir = PROJECT_ROOT / "template"
        if not template_dir.exists():
            return
        print(f"[Config] Applying template from {template_dir}...")
        for item in template_dir.iterdir():
            dest = WORKSPACE_DIR / item.name
            try:
                if item.is_dir():
                    if not dest.exists():
                        shutil.copytree(item, dest)
                        print(f"[Config] Copied template dir: {item.name}")
                    else:
                        # Merge: copy only items missing from the destination
                        for subitem in item.iterdir():
                            subdest = dest / subitem.name
                            if not subdest.exists():
                                if subitem.is_dir():
                                    shutil.copytree(subitem, subdest)
                                else:
                                    shutil.copy2(subitem, subdest)
                                print(f"[Config] Merged: {item.name}/{subitem.name}")
                elif not dest.exists():
                    shutil.copy2(item, dest)
                    print(f"[Config] Copied template file: {item.name}")
            except Exception as e:
                print(f"[Config] Error copying {item.name}: {e}")

    def _import_builtin_skills(self):
        """Copy builtin skill definitions from skills/ into the workspace (first run only)."""
        if not SIMPLECLAW_SKILLS_DIR.exists():
            return
        print(f"[Config] Importing builtin skills from {SIMPLECLAW_SKILLS_DIR}...")

        # Copy top-level skills README (overview)
        skills_readme = SIMPLECLAW_SKILLS_DIR / "README.md"
        dest_readme = WORKSPACE_DIR / "skills" / "README.md"
        if skills_readme.exists() and not dest_readme.exists():
            shutil.copy2(skills_readme, dest_readme)
            print("[Config] Imported skills overview: README.md")

        for skill_path in SIMPLECLAW_SKILLS_DIR.iterdir():
            if not skill_path.is_dir():
                continue
            skill_file = skill_path / "SKILL.md"
            if not skill_file.exists():
                continue

            dest_dir = WORKSPACE_DIR / "skills" / skill_path.name
            dest_dir.mkdir(parents=True, exist_ok=True)

            dest_skill = dest_dir / "SKILL.md"
            if not dest_skill.exists():
                shutil.copy2(skill_file, dest_skill)
                print(f"[Config] Imported skill: {skill_path.name}")

            # Copy the skill's README if present
            source_readme = skill_path / "README.md"
            dest_skill_readme = dest_dir / "README.md"
            if source_readme.exists() and not dest_skill_readme.exists():
                shutil.copy2(source_readme, dest_skill_readme)

    def _create_default_md_files(self):
        """Write default workspace context files (SOUL.md, USER.md, etc.) if missing."""
        for filename, content in DEFAULT_MD_FILES.items():
            file_path = WORKSPACE_DIR / filename
            if not file_path.exists():
                file_path.write_text(content, encoding="utf-8")
                print(f"[Config] Created default context file: {filename}")

    def _create_config_file(self):
        """Copy config template to workspace on first run, or create default if template missing."""
        if CONFIG_FILE.exists():
            return
        if CONFIG_TEMPLATE.exists():
            shutil.copy2(CONFIG_TEMPLATE, CONFIG_FILE)
            print(f"[Config] Copied config template to workspace: {CONFIG_FILE}")
        else:
            CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=4), encoding="utf-8")
            print(f"[Config] Created default config at: {CONFIG_FILE}")

    def load(self) -> AppConfig:
        """Load configuration from config.json and parse into dataclasses."""
        data = DEFAULT_CONFIG
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                print(f"[Config] Loaded configuration from {CONFIG_FILE}")
            except Exception as e:
                print(f"[Config] Error loading config: {e}")

        # Filter to valid keys only (ignores unknown JSON fields gracefully)
        llm_kwargs = {k: v for k, v in data.get("llm", {}).items() if hasattr(LLMConfig, k)}
        agent_kwargs = {k: v for k, v in data.get("agent", {}).items() if hasattr(AgentConfig, k)}
        heartbeat_kwargs = {k: v for k, v in data.get("heartbeat", {}).items() if hasattr(HeartbeatConfig, k)}
        telegram_kwargs = {k: v for k, v in data.get("telegram", {}).items() if hasattr(TelegramConfig, k)}
        wecom_kwargs = {k: v for k, v in data.get("wecom", {}).items() if hasattr(WecomConfig, k)}

        return AppConfig(
            llm=LLMConfig(**llm_kwargs),
            agent=AgentConfig(**agent_kwargs),
            cron=CronConfig(tasks=data.get("cron", {}).get("tasks", [])),
            heartbeat=HeartbeatConfig(**heartbeat_kwargs),
            telegram=TelegramConfig(**telegram_kwargs),
            wecom=WecomConfig(**wecom_kwargs),
        )


# Global singleton — imported throughout the codebase
config = ConfigLoader().config
