"""
GitHub API tools via PyGitHub.
Mirrors the MCP tool signatures used in SKILL.md files.
"""
import base64
import json
import os
from typing import Any, Optional

from github import Github, GithubException


# ─── Tool definitions (Anthropic tool format) ─────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "get_pull_request",
        "description": "Get details of a specific pull request",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
    },
    {
        "name": "get_pull_request_files",
        "description": "Get the list of files changed in a pull request",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "pull_number": {"type": "integer"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
    },
    {
        "name": "list_commits",
        "description": "Get list of commits of a branch",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "sha": {"type": "string", "description": "Branch name or commit SHA"},
                "per_page": {"type": "integer", "description": "Number of commits to return (default 10)"},
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "get_file_contents",
        "description": "Get the contents of a file from a GitHub repository",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "path": {"type": "string", "description": "Path to the file"},
                "branch": {"type": "string", "description": "Branch name (optional)"},
            },
            "required": ["owner", "repo", "path"],
        },
    },
    {
        "name": "create_pull_request_review",
        "description": "Create a review on a pull request",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "pull_number": {"type": "integer"},
                "event": {
                    "type": "string",
                    "enum": ["APPROVE", "REQUEST_CHANGES", "COMMENT"],
                },
                "body": {"type": "string", "description": "Review summary body"},
                "comments": {
                    "type": "array",
                    "description": "Inline review comments",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "line": {"type": "integer"},
                            "body": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["owner", "repo", "pull_number", "event", "body"],
        },
    },
    {
        "name": "create_branch",
        "description": "Create a new branch in a GitHub repository",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "branch": {"type": "string", "description": "New branch name"},
                "from_branch": {"type": "string", "description": "Source branch"},
            },
            "required": ["owner", "repo", "branch"],
        },
    },
    {
        "name": "create_or_update_file",
        "description": "Create or update a file in a GitHub repository",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "path": {"type": "string"},
                "content": {"type": "string", "description": "File content (plain text)"},
                "message": {"type": "string", "description": "Commit message"},
                "branch": {"type": "string"},
                "sha": {"type": "string", "description": "SHA of existing file (required for updates)"},
            },
            "required": ["owner", "repo", "path", "content", "message", "branch"],
        },
    },
    {
        "name": "create_pull_request",
        "description": "Create a new pull request",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "head": {"type": "string", "description": "Head branch"},
                "base": {"type": "string", "description": "Base branch"},
            },
            "required": ["owner", "repo", "title", "head", "base"],
        },
    },
    {
        "name": "add_issue_comment",
        "description": "Add a comment to a PR/issue",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "issue_number": {"type": "integer"},
                "body": {"type": "string"},
            },
            "required": ["owner", "repo", "issue_number", "body"],
        },
    },
    {
        "name": "get_pull_request_status",
        "description": "Get the combined status of all status checks for a pull request",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "pull_number": {"type": "integer"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
    },
    {
        "name": "detect_test_framework",
        "description": "Detect the test framework used in a repository by inspecting requirements.txt, package.json, or pyproject.toml",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "branch": {"type": "string", "description": "Branch to check (optional, defaults to default branch)"},
            },
            "required": ["owner", "repo"],
        },
    },
]


# ─── GitHubTools implementation ───────────────────────────────────────────────

class GitHubTools:
    def __init__(self, token: Optional[str] = None):
        token = token or os.environ.get("GITHUB_TOKEN", "")
        if token:
            self._gh = Github(token)
        else:
            self._gh = Github()  # Unauthenticated (60 req/hr limit)

    def _get_repo(self, owner: str, repo: str):
        return self._gh.get_repo(f"{owner}/{repo}")

    def get_pull_request(self, owner: str, repo: str, pull_number: int) -> dict:
        pr = self._get_repo(owner, repo).get_pull(pull_number)
        return {
            "number": pr.number,
            "title": pr.title,
            "body": pr.body or "",
            "state": pr.state,
            "draft": pr.draft,
            "author": pr.user.login,
            "base_branch": pr.base.ref,
            "head_branch": pr.head.ref,
            "head_sha": pr.head.sha,
            "additions": pr.additions,
            "deletions": pr.deletions,
            "changed_files": pr.changed_files,
            "merged": pr.merged,
            "html_url": pr.html_url,
        }

    def get_pull_request_files(self, owner: str, repo: str, pull_number: int) -> list[dict]:
        pr = self._get_repo(owner, repo).get_pull(pull_number)
        files = []
        for f in pr.get_files():
            files.append({
                "filename": f.filename,
                "status": f.status,
                "additions": f.additions,
                "deletions": f.deletions,
                "changes": f.changes,
                "patch": f.patch or "",
            })
        return files

    def list_commits(
        self,
        owner: str,
        repo: str,
        sha: Optional[str] = None,
        per_page: int = 10,
    ) -> list[dict]:
        kwargs = {}
        if sha:
            kwargs["sha"] = sha
        commits_paged = self._get_repo(owner, repo).get_commits(**kwargs)
        result = []
        for c in commits_paged[:per_page]:
            result.append({
                "sha": c.sha,
                "message": c.commit.message,
                "author": c.commit.author.name if c.commit.author else "",
                "date": c.commit.author.date.isoformat() if c.commit.author else "",
            })
        return result

    def get_file_contents(
        self,
        owner: str,
        repo: str,
        path: str,
        branch: Optional[str] = None,
    ) -> dict:
        kwargs = {}
        if branch:
            kwargs["ref"] = branch
        contents = self._get_repo(owner, repo).get_contents(path, **kwargs)
        # get_contents can return a list for directories; we expect a single file
        if isinstance(contents, list):
            raise ValueError(f"{path} is a directory")
        decoded = base64.b64decode(contents.content).decode("utf-8", errors="replace")
        return {
            "path": contents.path,
            "sha": contents.sha,
            "content": decoded,
            "encoding": contents.encoding,
            "size": contents.size,
        }

    def create_pull_request_review(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        event: str,
        body: str,
        comments: Optional[list[dict]] = None,
    ) -> dict:
        pr = self._get_repo(owner, repo).get_pull(pull_number)
        # Build inline comment dicts for batch submission with the review
        review_comments = []
        if comments:
            for c in comments:
                if c.get("line") is not None and c.get("path") and c.get("body"):
                    review_comments.append({
                        "path": c["path"],
                        "line": c["line"],
                        "body": c["body"],
                    })

        # Submit review with inline comments in a single API call
        if review_comments:
            review = pr.create_review(body=body, event=event, comments=review_comments)
        else:
            review = pr.create_review(body=body, event=event)
        return {"id": review.id, "state": review.state, "body": review.body}

    def create_branch(
        self,
        owner: str,
        repo: str,
        branch: str,
        from_branch: Optional[str] = None,
    ) -> dict:
        repo_obj = self._get_repo(owner, repo)
        if from_branch:
            source_ref = repo_obj.get_branch(from_branch)
            sha = source_ref.commit.sha
        else:
            sha = repo_obj.get_branch(repo_obj.default_branch).commit.sha

        ref = repo_obj.create_git_ref(ref=f"refs/heads/{branch}", sha=sha)
        return {"ref": ref.ref, "sha": sha}

    def create_or_update_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str,
        sha: Optional[str] = None,
    ) -> dict:
        repo_obj = self._get_repo(owner, repo)
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")

        if sha:
            result = repo_obj.update_file(
                path=path,
                message=message,
                content=content,
                sha=sha,
                branch=branch,
            )
        else:
            result = repo_obj.create_file(
                path=path,
                message=message,
                content=content,
                branch=branch,
            )
        return {
            "path": result["content"].path,
            "sha": result["content"].sha,
            "commit_sha": result["commit"].sha,
        }

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str = "",
    ) -> dict:
        pr = self._get_repo(owner, repo).create_pull(
            title=title,
            body=body,
            head=head,
            base=base,
        )
        return {
            "number": pr.number,
            "title": pr.title,
            "html_url": pr.html_url,
            "head": pr.head.ref,
            "base": pr.base.ref,
        }

    def add_issue_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> dict:
        issue = self._get_repo(owner, repo).get_issue(issue_number)
        comment = issue.create_comment(body)
        return {"id": comment.id, "html_url": comment.html_url}

    def get_pull_request_status(
        self,
        owner: str,
        repo: str,
        pull_number: int,
    ) -> dict:
        pr = self._get_repo(owner, repo).get_pull(pull_number)
        commit = self._get_repo(owner, repo).get_commit(pr.head.sha)
        combined = commit.get_combined_status()
        return {
            "state": combined.state,
            "statuses": [
                {
                    "context": s.context,
                    "state": s.state,
                    "description": s.description or "",
                    "target_url": s.target_url or "",
                }
                for s in combined.statuses
            ],
        }

    def detect_test_framework(
        self,
        owner: str,
        repo: str,
        branch: Optional[str] = None,
    ) -> dict:
        """
        Detect the test framework used in a repository by inspecting
        requirements.txt, package.json, and pyproject.toml.
        """
        repo_obj = self._get_repo(owner, repo)
        kwargs = {}
        if branch:
            kwargs["ref"] = branch

        frameworks = []
        language = "unknown"
        config_files_checked = []

        # Check Python requirements.txt
        try:
            contents = repo_obj.get_contents("requirements.txt", **kwargs)
            text = base64.b64decode(contents.content).decode("utf-8", errors="replace")
            config_files_checked.append("requirements.txt")
            language = "python"
            if "pytest" in text:
                frameworks.append("pytest")
            if "unittest" in text or "unittest2" in text:
                frameworks.append("unittest")
            if "nose" in text or "nose2" in text:
                frameworks.append("nose")
        except Exception:
            pass

        # Check pyproject.toml (pytest, poetry)
        try:
            contents = repo_obj.get_contents("pyproject.toml", **kwargs)
            text = base64.b64decode(contents.content).decode("utf-8", errors="replace")
            config_files_checked.append("pyproject.toml")
            language = "python"
            if "pytest" in text and "pytest" not in frameworks:
                frameworks.append("pytest")
        except Exception:
            pass

        # Check package.json (JavaScript/TypeScript)
        try:
            contents = repo_obj.get_contents("package.json", **kwargs)
            text = base64.b64decode(contents.content).decode("utf-8", errors="replace")
            config_files_checked.append("package.json")
            pkg = json.loads(text)
            language = "javascript"
            all_deps = {}
            all_deps.update(pkg.get("dependencies", {}))
            all_deps.update(pkg.get("devDependencies", {}))
            if "jest" in all_deps:
                frameworks.append("jest")
            if "mocha" in all_deps:
                frameworks.append("mocha")
            if "vitest" in all_deps:
                frameworks.append("vitest")
            if "jasmine" in all_deps:
                frameworks.append("jasmine")
            # Check test script
            scripts = pkg.get("scripts", {})
            test_script = scripts.get("test", "")
            if "jest" in test_script and "jest" not in frameworks:
                frameworks.append("jest")
            if "mocha" in test_script and "mocha" not in frameworks:
                frameworks.append("mocha")
        except Exception:
            pass

        # Fallback: detect language from file extensions if no config found
        if not frameworks:
            if language == "python":
                frameworks = ["pytest"]  # Default Python test framework
            elif language == "javascript":
                frameworks = ["jest"]  # Default JS test framework

        return {
            "language": language,
            "frameworks": frameworks,
            "primary_framework": frameworks[0] if frameworks else "unknown",
            "config_files_checked": config_files_checked,
        }


def detect_test_framework(github_tools: "GitHubTools", owner: str, repo: str, branch: Optional[str] = None) -> dict:
    """Module-level wrapper for detect_test_framework."""
    return github_tools.detect_test_framework(owner, repo, branch)
