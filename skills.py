import os
import glob
from pathlib import Path
from typing import List, Dict

class SkillsLoader:
    """Loads and provides skills (tools) from the filesystem."""
    def __init__(self, workspace: Path):
        self.skills_dir = workspace / "skills"
        if not self.skills_dir.exists():
            self.skills_dir.mkdir(parents=True)
            print(f"[Skills] Created skill directory: {self.skills_dir}")
        self.skills = {}

    def discover_skills(self):
        """Scans the skills directory for valid skill folders (not just SKILL.md)."""
        # Assume each subdirectory in skills/ is a skill folder
        try:
            for skill_dir in self.skills_dir.iterdir():
                if skill_dir.is_dir():
                    skill_name = skill_dir.name
                    desc_file = skill_dir / "SKILL.md"
                    
                    description = ""
                    if desc_file.exists():
                        description = desc_file.read_text(encoding="utf-8")
                        self.skills[skill_name] = description
                        print(f"[Skills] Found skill: {skill_name}")
        except FileNotFoundError:
            pass

    def get_skill_prompts(self) -> str:
        """Combine all skills descriptions into a single prompt section."""
        if not self.skills:
            return ""
        
        prompt_lines = ["\n## Available Skills (Agent Capabilities)\nThe following skills are available as tools or contextual help:\n"]
        for name, desc in self.skills.items():
            prompt_lines.append(f"### {name.title()} Skill\n{desc}\n")
        return "\n".join(prompt_lines)

    def get_definition(self, name: str) -> str:
        return self.skills.get(name, "")

