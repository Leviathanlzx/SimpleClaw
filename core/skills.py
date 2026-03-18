import re
import json
from pathlib import Path
from typing import List, Dict, Tuple

class SkillsLoader:
    """Loads and provides skills (tools) from the filesystem."""
    def __init__(self, workspace: Path):
        self.skills_dir = workspace / "skills"
        if not self.skills_dir.exists():
            self.skills_dir.mkdir(parents=True)
            print(f"[Skills] Created skill directory: {self.skills_dir}")
        self.skills_info = {}

    def discover_skills(self):
        """Scans the skills directory and parses metadata."""
        self.skills_info = {}
        try:
            for skill_dir in self.skills_dir.iterdir():
                if skill_dir.is_dir():
                    skill_name = skill_dir.name
                    desc_file = skill_dir / "SKILL.md"
                    
                    if desc_file.exists():
                        raw_content = desc_file.read_text(encoding="utf-8")
                        frontmatter, body = self._split_frontmatter(raw_content)
                        metadata = self._parse_frontmatter_data(frontmatter)
                        
                        # Fallback description
                        description = metadata.get("description", "No description provided.")
                        
                        self.skills_info[skill_name] = {
                            "path": str(desc_file).replace("\\", "/"), # Ensure posix style for consistent path usage
                            "description": description,
                            "metadata": metadata,
                            "content": body
                        }
                        print(f"[Skills] Discovered skill: {skill_name}")
        except FileNotFoundError:
            pass

    def _split_frontmatter(self, content: str) -> Tuple[str, str]:
        """Split content into frontmatter and body."""
        if content.startswith("---"):
            parts = re.split(r"^---\s*$", content, maxsplit=2, flags=re.MULTILINE)
            if len(parts) >= 3:
                return parts[1].strip(), parts[2].strip()
        return "", content

    def _parse_frontmatter_data(self, frontmatter: str) -> Dict:
        """Simple key: value frontmatter parser (no full YAML dependency needed)."""
        data = {}
        if not frontmatter:
            return data
        for line in frontmatter.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                data[key.strip()] = value.strip()
        return data

    def get_always_skills_content(self) -> str:
        """Returns the content of skills that should always be loaded."""
        parts = []
        for name, info in self.skills_info.items():
            # A skill is always-on if its frontmatter has `always: true`,
            # or if it's one of the known core skills.
            metadata = info.get("metadata", {})
            is_always = metadata.get("always", "").lower() == "true"
            if name in ("memory", "planner"):
                is_always = True

            if is_always:
                parts.append(f"### Skill: {name}\n\n{info['content']}")

        return "\n\n---\n\n".join(parts) if parts else ""

    def build_skills_summary(self) -> str:
        """
        Build a Markdown summary of available skills.
        Each entry shows the skill name, description, and file path.
        The LLM must call read_file on the path to load full instructions.
        """
        if not self.skills_info:
            return ""

        lines = [
            "# Available Skills",
            "To use a skill, read its file first with `read_file` to get full instructions.",
            "",
        ]
        for name, info in self.skills_info.items():
            lines.append(f"- **{name}** — {info['description']}")
            lines.append(f"  File: `{info['path']}`")

        return "\n".join(lines)

    def get_skill_prompts(self) -> str:
        """Combine skills summary and always-on content for the agent prompt."""
        summary = self.build_skills_summary()
        always_content = self.get_always_skills_content()
        
        parts = []
        if summary:
            parts.append(summary)
        if always_content:
            parts.append(always_content)
            
        return "\n\n".join(parts)
