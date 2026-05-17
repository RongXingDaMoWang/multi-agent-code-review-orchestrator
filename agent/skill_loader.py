"""
SkillLoader: reads SKILL.md files from ~/.claude/skills/ and builds system prompts.
"""
import re
from pathlib import Path
from typing import Optional


# Project-local skills dir (preferred), fallback to ~/.claude/skills
_PROJECT_SKILLS_DIR = Path(__file__).parent.parent / "skills"
SKILLS_DIR = _PROJECT_SKILLS_DIR if _PROJECT_SKILLS_DIR.exists() else Path.home() / ".claude" / "skills"


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter (--- ... ---) from skill markdown."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


class SkillLoader:
    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = skills_dir or SKILLS_DIR

    def load_skill(self, skill_name: str, include_references: bool = True) -> str:
        """
        Load a skill's SKILL.md and optionally its references/ directory.
        Returns the combined system prompt string.
        """
        skill_dir = self.skills_dir / skill_name
        skill_md_path = skill_dir / "SKILL.md"

        if not skill_md_path.exists():
            raise FileNotFoundError(f"Skill not found: {skill_md_path}")

        body = _strip_frontmatter(skill_md_path.read_text(encoding="utf-8"))

        if not include_references:
            return body

        # Load references
        refs_dir = skill_dir / "references"
        ref_sections = []
        if refs_dir.exists():
            for ref_file in sorted(refs_dir.glob("*.md")):
                ref_content = ref_file.read_text(encoding="utf-8")
                ref_sections.append(
                    f"===== Reference: {ref_file.name} =====\n\n{ref_content}"
                )

        if ref_sections:
            return body + "\n\n" + "\n\n".join(ref_sections)
        return body

    def load_combined(self, *skill_names: str) -> str:
        """Load multiple skills and combine into one system prompt."""
        parts = []
        for name in skill_names:
            try:
                content = self.load_skill(name, include_references=True)
                parts.append(f"===== {name} =====\n\n{content}")
            except FileNotFoundError:
                pass
        return "\n\n".join(parts)
