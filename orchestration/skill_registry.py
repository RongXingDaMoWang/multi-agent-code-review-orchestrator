"""
SkillRegistry: dynamic discovery + role-based matching of skills and MCP tools.

- discover_skills()    scans skills/<name>/SKILL.md, parses YAML frontmatter
                       (name, description, tags) without a YAML dependency
- discover_mcp_tools() reads .kiro/settings/mcp.json + each server's .py file
                       (AST-walks `@mcp.tool()` decorators) to enumerate the
                       registered tool surface
- match_skills_for_role(role)  returns skill names whose tags intersect the
                                role's wanted-tag set
- match_tools_for_role(role)   returns MCP tool names recommended for the role

Phase 3 capability. Independent of Leader/Worker — the orchestrator can
consult it but does not have to.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .schemas import WorkerRole

# Project-relative defaults; explicit paths override.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SKILLS_DIR = _PROJECT_ROOT / "skills"
_DEFAULT_MCP_CONFIG = _PROJECT_ROOT / ".kiro" / "settings" / "mcp.json"


@dataclass
class SkillMeta:
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    path: str = ""


@dataclass
class ToolMeta:
    name: str
    description: str
    server: str
    mcp_command: str = ""


# Tags each role wants to see in matching skills. Common-purpose skills
# (analyze, act, memory, triage, review) overlap with every role via shared
# tags, while role-specific tags ("security" / "performance" / etc.) come
# from the analyze skill which is tagged for all four.
ROLE_SKILL_TAGS: dict[WorkerRole, set[str]] = {
    "security":     {"security", "analysis", "action", "memory", "triage", "review"},
    "performance":  {"performance", "analysis", "action", "memory", "triage", "review"},
    "architecture": {"architecture", "analysis", "action", "memory", "triage", "review"},
    "style":        {"style", "analysis", "action", "memory", "triage", "review"},
}

# MCP tool subset recommended for each role. The Worker layer's
# ScopedToolRouter still does the actual enforcement — this map is the
# orchestrator's discovery hint.
ROLE_TOOLS: dict[WorkerRole, list[str]] = {
    "security": [
        "get_pull_request_files", "get_file_contents",
        "detect_security_issues", "extract_functions",
        "search_known_patterns", "get_developer_profile",
    ],
    "performance": [
        "get_pull_request_files", "get_file_contents",
        "analyze_complexity", "extract_functions",
        "search_known_patterns", "get_developer_profile",
    ],
    "architecture": [
        "get_pull_request_files", "get_file_contents", "list_commits",
        "analyze_complexity", "extract_functions",
        "search_known_patterns", "get_developer_profile",
    ],
    "style": [
        "get_pull_request_files", "get_file_contents",
        "search_known_patterns", "get_developer_profile",
    ],
}


class SkillRegistry:
    """Skill 动态发现与注册中心.

    Caches results after first discovery; call refresh() to re-scan.
    """

    def __init__(
        self,
        skills_dir: Optional[Path] = None,
        mcp_config: Optional[Path] = None,
    ):
        self.skills_dir = skills_dir or _DEFAULT_SKILLS_DIR
        self.mcp_config = mcp_config or _DEFAULT_MCP_CONFIG
        self._skills_cache: Optional[list[SkillMeta]] = None
        self._tools_cache: Optional[list[ToolMeta]] = None

    # ── Public API ────────────────────────────────────────────────────────

    def discover_skills(self, refresh: bool = False) -> list[SkillMeta]:
        """Scan skills_dir; return one SkillMeta per SKILL.md found."""
        if self._skills_cache is not None and not refresh:
            return self._skills_cache
        skills: list[SkillMeta] = []
        if not self.skills_dir.exists():
            self._skills_cache = skills
            return skills
        for skill_dir in sorted(p for p in self.skills_dir.iterdir() if p.is_dir()):
            md = skill_dir / "SKILL.md"
            if not md.exists():
                continue
            meta = _parse_frontmatter(md)
            if meta is not None:
                meta.path = str(md)
                skills.append(meta)
        self._skills_cache = skills
        return skills

    def discover_mcp_tools(self, refresh: bool = False) -> list[ToolMeta]:
        """Read mcp.json + walk each server file's `@mcp.tool()` decorators."""
        if self._tools_cache is not None and not refresh:
            return self._tools_cache
        tools: list[ToolMeta] = []
        if not self.mcp_config.exists():
            self._tools_cache = tools
            return tools
        try:
            cfg = json.loads(self.mcp_config.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self._tools_cache = tools
            return tools

        for server_name, entry in (cfg.get("mcpServers") or {}).items():
            args = entry.get("args") or []
            command = entry.get("command", "")
            mcp_command = f"{command} {' '.join(args)}".strip()
            server_path = next((Path(_PROJECT_ROOT, a) for a in args
                                if a.endswith(".py")), None)
            if server_path is None or not server_path.exists():
                continue
            for tool_name, tool_doc in _ast_extract_mcp_tools(server_path):
                tools.append(ToolMeta(
                    name=tool_name, description=tool_doc,
                    server=server_name, mcp_command=mcp_command,
                ))
        self._tools_cache = tools
        return tools

    def match_skills_for_role(self, role: WorkerRole) -> list[str]:
        """Return SKILL names whose tags intersect the role's wanted set."""
        wanted = ROLE_SKILL_TAGS.get(role, set())
        return [
            s.name for s in self.discover_skills()
            if wanted & set(s.tags)
        ]

    def match_tools_for_role(self, role: WorkerRole) -> list[str]:
        """Return MCP tool names recommended for the role, filtered to those
        actually present in the registered MCP servers."""
        recommended = ROLE_TOOLS.get(role, [])
        available = {t.name for t in self.discover_mcp_tools()}
        if not available:
            return list(recommended)
        return [t for t in recommended if t in available]


# ─── Frontmatter parsing (no PyYAML dep) ──────────────────────────────────────

_TAGS_RE = re.compile(r"^tags:\s*\[([^\]]*)\]\s*$", re.MULTILINE)
_NAME_RE = re.compile(r"^name:\s*(.+)$", re.MULTILINE)
_DESC_RE = re.compile(
    r"^description:\s*(?:\|\s*\n((?:[ \t]+.*\n?)+)|(.+))$", re.MULTILINE
)


def _parse_frontmatter(md_path: Path) -> Optional[SkillMeta]:
    text = md_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    fm = text[3:end + 1]

    name_m = _NAME_RE.search(fm)
    if not name_m:
        return None

    desc_m = _DESC_RE.search(fm)
    description = ""
    if desc_m:
        block, inline = desc_m.group(1), desc_m.group(2)
        description = (block or inline or "").strip()

    tags: list[str] = []
    tags_m = _TAGS_RE.search(fm)
    if tags_m:
        tags = [t.strip().strip("'\"") for t in tags_m.group(1).split(",")
                if t.strip()]

    return SkillMeta(name=name_m.group(1).strip(), description=description, tags=tags)


def _ast_extract_mcp_tools(server_py: Path) -> Iterable[tuple[str, str]]:
    """Yield (tool_name, first_line_of_docstring) for each `@mcp.tool()` def."""
    try:
        tree = ast.parse(server_py.read_text(encoding="utf-8"))
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(_is_mcp_tool_decorator(d) for d in node.decorator_list):
            continue
        doc = ast.get_docstring(node) or ""
        first_line = doc.splitlines()[0].strip() if doc else ""
        yield node.name, first_line


def _is_mcp_tool_decorator(d: ast.expr) -> bool:
    """Match `@mcp.tool()` or `@mcp.tool` regardless of args."""
    target = d.func if isinstance(d, ast.Call) else d
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "tool"
        and isinstance(target.value, ast.Name)
        and target.value.id == "mcp"
    )
