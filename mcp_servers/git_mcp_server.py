"""
MCP Server wrapping GitHub tools from tools/github_tools.py.

Exposes 11 tools for any MCP-compatible agent to discover and invoke.
The underlying GitHubTools implementation is imported unchanged; this
layer only adds MCP plumbing and the patch/file truncation that lived
in agent/tool_router.py.

Run standalone:
    python mcp_servers/git_mcp_server.py
"""
import os
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.github_tools import GitHubTools  # noqa: E402

MAX_PATCH_CHARS = 3000
MAX_FILE_CONTENT_CHARS = 5000

mcp = FastMCP("git")
_gh = GitHubTools(token=os.environ.get("GITHUB_TOKEN", ""))


@mcp.tool()
def get_pull_request(owner: str, repo: str, pull_number: int) -> dict:
    """Get details of a specific pull request."""
    return _gh.get_pull_request(owner, repo, pull_number)


@mcp.tool()
def get_pull_request_files(owner: str, repo: str, pull_number: int) -> list[dict]:
    """Get the list of files changed in a pull request.
    Patches are truncated to 3000 chars/file to bound context size."""
    files = _gh.get_pull_request_files(owner, repo, pull_number)
    for f in files:
        patch = f.get("patch", "") or ""
        if len(patch) > MAX_PATCH_CHARS:
            f["patch"] = (
                patch[:MAX_PATCH_CHARS]
                + f"\n... [truncated {len(patch) - MAX_PATCH_CHARS} chars]"
            )
    return files


@mcp.tool()
def list_commits(
    owner: str, repo: str, sha: Optional[str] = None, per_page: int = 10
) -> list[dict]:
    """Get list of commits of a branch."""
    return _gh.list_commits(owner, repo, sha=sha, per_page=per_page)


@mcp.tool()
def get_file_contents(
    owner: str, repo: str, path: str, branch: Optional[str] = None
) -> dict:
    """Get the contents of a file from a GitHub repository.
    Content is truncated to 5000 chars to bound context size."""
    result = _gh.get_file_contents(owner, repo, path, branch=branch)
    content = result.get("content", "") or ""
    if len(content) > MAX_FILE_CONTENT_CHARS:
        result["content"] = content[:MAX_FILE_CONTENT_CHARS] + "\n... [truncated]"
    return result


@mcp.tool()
def create_pull_request_review(
    owner: str,
    repo: str,
    pull_number: int,
    event: str,
    body: str,
    comments: Optional[list[dict]] = None,
) -> dict:
    """Create a review on a pull request. event ∈ {APPROVE, REQUEST_CHANGES, COMMENT}."""
    return _gh.create_pull_request_review(
        owner, repo, pull_number, event, body, comments=comments
    )


@mcp.tool()
def create_branch(
    owner: str, repo: str, branch: str, from_branch: Optional[str] = None
) -> dict:
    """Create a new branch in a GitHub repository."""
    return _gh.create_branch(owner, repo, branch, from_branch=from_branch)


@mcp.tool()
def create_or_update_file(
    owner: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str,
    sha: Optional[str] = None,
) -> dict:
    """Create or update a file in a GitHub repository.
    Pass `sha` of the existing blob when updating."""
    return _gh.create_or_update_file(
        owner, repo, path, content, message, branch, sha=sha
    )


@mcp.tool()
def create_pull_request(
    owner: str, repo: str, title: str, head: str, base: str, body: str = ""
) -> dict:
    """Create a new pull request."""
    return _gh.create_pull_request(owner, repo, title, head, base, body=body)


@mcp.tool()
def add_issue_comment(owner: str, repo: str, issue_number: int, body: str) -> dict:
    """Add a comment to a PR/issue."""
    return _gh.add_issue_comment(owner, repo, issue_number, body)


@mcp.tool()
def get_pull_request_status(owner: str, repo: str, pull_number: int) -> dict:
    """Get the combined status of all status checks for a pull request."""
    return _gh.get_pull_request_status(owner, repo, pull_number)


@mcp.tool()
def detect_test_framework(
    owner: str, repo: str, branch: Optional[str] = None
) -> dict:
    """Detect the test framework used in a repository by inspecting
    requirements.txt, package.json, or pyproject.toml."""
    return _gh.detect_test_framework(owner, repo, branch=branch)


if __name__ == "__main__":
    mcp.run()
