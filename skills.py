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
        """Simple YAML-like parsing for frontmatter (key: value)."""
        data = {}
        if not frontmatter:
            return data
            
        current_key = None
        for line in frontmatter.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
                
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip()
                data[key] = value
                current_key = key
            # Very basic handling for JSON in metadata or multi-line values could go here
            # For now we assume simple key: value structure similar to the examples
            
        # Specific fix for metadata json string if present
        if "metadata" in data and data["metadata"].startswith("{"):
            try:
                # This is a hacky way to get the full json string if it was on one line
                # Ideally we'd use a real yaml parser
                pass
            except:
                pass
                
        return data

    def get_always_skills_content(self) -> str:
        """Returns the content of skills that should always be loaded."""
        parts = []
        for name, info in self.skills_info.items():
            # Check for always=true in metadata (parsing json inside metadata field is tricky without proper parser)
            # For now, let's look for "always" in the string representation or specific names
            metadata_str = str(info.get("metadata", ""))
            
            # Simple heuristic for 'always' or core skills
            is_always = "always" in metadata_str and "true" in metadata_str.lower()
            if name in ["memory", "planner"]: # Force specific core skills to be always on
                is_always = True

            if is_always:
                parts.append(f"### Skill: {name}\n\n{info['content']}")
                
        return "\n\n---\n\n".join(parts) if parts else ""

    def build_skills_summary(self) -> str:
        """
        Builds an XML summary of available skills for the Context Window.
        This forces the LLM to 'read_file' to learn the skill first.
        """
        if not self.skills_info:
            return ""
        
        lines = ["# Available Skills", 
                 "The following skills extend your capabilities. To use a skill, you must first read its instructions file using `read_file`.",
                 "<skills>"]
                 
        for name, info in self.skills_info.items():
            # Skip if it's already loaded in 'always' (optional optimization)
            # For now list everything in summary so agent knows where to look
            
            lines.append(f'  <skill name="{name}">')
            lines.append(f'    <description>{info["description"]}</description>')
            lines.append(f'    <location>{info["path"]}</location>')
            lines.append(f'  </skill>')
            
        lines.append("</skills>")
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
