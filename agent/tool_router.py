"""
Tool router: maps tool names to Python functions.
Handles both GitHub tools and memory tools.
"""
import json
from typing import Any, Callable

from ..tools.github_tools import GitHubTools, TOOL_DEFINITIONS as GITHUB_TOOL_DEFS
from ..tools.memory_tools import MemoryTools, TOOL_DEFINITIONS as MEMORY_TOOL_DEFS


ALL_TOOL_DEFINITIONS = GITHUB_TOOL_DEFS + MEMORY_TOOL_DEFS


class ToolRouter:
    def __init__(self, github_token: str = "", dry_run: bool = False):
        self._github = GitHubTools(token=github_token)
        self._memory = MemoryTools()
        self._dry_run = dry_run

        # Build dispatch table
        self._dispatch: dict[str, Callable] = {
            # GitHub tools
            "get_pull_request": self._github.get_pull_request,
            "get_pull_request_files": self._github.get_pull_request_files,
            "list_commits": self._github.list_commits,
            "get_file_contents": self._github.get_file_contents,
            "create_pull_request_review": self._github.create_pull_request_review,
            "create_branch": self._github.create_branch,
            "create_or_update_file": self._github.create_or_update_file,
            "create_pull_request": self._github.create_pull_request,
            "add_issue_comment": self._github.add_issue_comment,
            "get_pull_request_status": self._github.get_pull_request_status,
            "detect_test_framework": self._github.detect_test_framework,
            # Memory tools
            "memory_get_all": self._memory.memory_get_all,
            "memory_add_pattern": self._memory.memory_add_pattern,
            "memory_add_false_positive": self._memory.memory_add_false_positive,
            "memory_get_developer_profile": self._memory.memory_get_developer_profile,
            "memory_update_developer_profile": self._memory.memory_update_developer_profile,
            "memory_aggregate_patterns": self._memory.memory_aggregate_patterns,
        }

        # Write tools that are skipped in dry-run mode
        self._write_tools = {
            "create_pull_request_review",
            "create_branch",
            "create_or_update_file",
            "create_pull_request",
            "add_issue_comment",
            "memory_add_pattern",
            "memory_add_false_positive",
            "memory_update_developer_profile",
            "memory_aggregate_patterns",
        }

    # Max chars for patch content per file (prevent context overflow)
    MAX_PATCH_CHARS = 3000
    # Max chars for full file content
    MAX_FILE_CONTENT_CHARS = 5000

    def execute(self, tool_name: str, tool_input: dict) -> Any:
        """
        Execute a tool by name with the given input dict.
        Returns the result (will be JSON-serialized for the trajectory).
        """
        if tool_name not in self._dispatch:
            return {"error": f"Unknown tool: {tool_name}"}

        if self._dry_run and tool_name in self._write_tools:
            return {
                "dry_run": True,
                "tool": tool_name,
                "input": tool_input,
                "message": f"[DRY RUN] Would call {tool_name} with {json.dumps(tool_input)[:200]}",
            }

        try:
            fn = self._dispatch[tool_name]
            result = fn(**tool_input)
            result = self._truncate_result(tool_name, result)
            return result
        except Exception as e:
            return {"error": str(e), "tool": tool_name}

    def _truncate_result(self, tool_name: str, result: Any) -> Any:
        """Truncate large fields to prevent context window overflow."""
        if tool_name == "get_pull_request_files" and isinstance(result, list):
            for f in result:
                if isinstance(f, dict) and "patch" in f:
                    patch = f["patch"]
                    if len(patch) > self.MAX_PATCH_CHARS:
                        f["patch"] = patch[:self.MAX_PATCH_CHARS] + f"\n... [truncated {len(patch)-self.MAX_PATCH_CHARS} chars]"
        elif tool_name == "get_file_contents" and isinstance(result, dict):
            content = result.get("content", "")
            if len(content) > self.MAX_FILE_CONTENT_CHARS:
                result["content"] = content[:self.MAX_FILE_CONTENT_CHARS] + f"\n... [truncated]"
        return result

    @property
    def tool_definitions(self) -> list[dict]:
        return ALL_TOOL_DEFINITIONS
