"""
MCP Server wrapping memory tools from tools/memory_tools.py.

Exposes 6 tools for review-memory operations. Underlying implementations
in tools/memory_tools.py are imported unchanged; this layer renames the
public surface to drop the `memory_*` prefix and presents friendlier
verbs (search/add/mark) for cross-agent reuse.

Run standalone:
    python mcp_servers/memory_mcp_server.py
"""
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import memory_tools as _mem  # noqa: E402

mcp = FastMCP("memory")


@mcp.tool()
def search_known_patterns(max_tokens: int = 3000) -> str:
    """Return a formatted summary of review rules, known patterns, and
    false-positive filters from review_memory.jsonl. Capped at max_tokens
    (approx 4 chars/token)."""
    return _mem.get_all_summary(max_tokens=max_tokens)


@mcp.tool()
def add_pattern(
    name: str,
    description: str,
    severity: str = "high",
    indicators: Optional[list[str]] = None,
) -> dict:
    """Add a new known-issue pattern to the review memory.
    severity ∈ {critical, high, medium, low}."""
    record = _mem.add_pattern(name, description, severity, indicators)
    return {"status": "ok", "id": record["id"]}


@mcp.tool()
def mark_false_positive(
    pattern: str,
    reason: str,
    file_patterns: Optional[list[str]] = None,
) -> dict:
    """Mark a pattern as a false positive in certain file contexts so it
    is suppressed in future reviews (e.g., hard-coded test data)."""
    record = _mem.add_false_positive(pattern, reason, file_patterns)
    return {"status": "ok", "id": record["id"]}


@mcp.tool()
def get_developer_profile(author: str) -> dict:
    """Get the developer growth profile for a GitHub user, including
    issue history, strengths, and growth areas. Returns a stub profile
    when the developer has no prior reviews."""
    profile = _mem.get_developer_profile(author)
    if profile:
        return {"found": True, "profile": profile}
    return {
        "found": False,
        "profile": {
            "author": author,
            "issue_history": [],
            "strengths": [],
            "growth_areas": [],
            "pr_count": 0,
            "message": "No profile yet — first review for this developer",
        },
    }


@mcp.tool()
def update_developer_profile(
    author: str,
    new_issues: Optional[list[dict]] = None,
    strengths: Optional[list[str]] = None,
    growth_areas: Optional[list[str]] = None,
) -> dict:
    """Update (or create) a developer profile after a review.
    new_issues items: {category, severity}."""
    record = _mem.update_developer_profile(author, new_issues, strengths, growth_areas)
    return {
        "status": "ok",
        "id": record["id"],
        "pr_count": record["content"]["pr_count"],
    }


@mcp.tool()
def aggregate_repo_patterns(repo: str, issues: list[dict]) -> dict:
    """Aggregate issues from a review into cross-PR repo_pattern records.
    issues items: {category, severity, file, description}. repo is in
    `owner/repo` form."""
    updated = _mem.aggregate_repo_patterns(repo, issues)
    return {"status": "ok", "patterns_updated": len(updated), "repo": repo}


if __name__ == "__main__":
    mcp.run()
