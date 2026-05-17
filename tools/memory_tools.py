"""
review_memory.jsonl read/write tools.
Mirrors the code-review-memory skill behavior.
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

MEMORY_FILE = Path.home() / ".claude" / "review_memory.jsonl"

BUILTIN_RULES = [
    {"type": "known_pattern", "id": "builtin-1",
     "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
     "content": {"name": "SQL注入", "description": "使用字符串拼接或 f-string 构造 SQL 查询",
                 "severity": "critical",
                 "indicators": ["f\"SELECT", "f'SELECT", "+ \" WHERE", "+ ' WHERE"]}},
    {"type": "known_pattern", "id": "builtin-2",
     "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
     "content": {"name": "硬编码密钥", "description": "代码中直接包含 API key、密码、token",
                 "severity": "critical",
                 "indicators": ["password =", "api_key =", "secret =", "token ="]}},
    {"type": "known_pattern", "id": "builtin-3",
     "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
     "content": {"name": "XSS漏洞", "description": "未转义的用户输入直接渲染到 HTML",
                 "severity": "high",
                 "indicators": ["innerHTML =", "dangerouslySetInnerHTML", "render_template_string"]}},
    {"type": "false_positive", "id": "builtin-fp-1",
     "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
     "content": {"pattern": "test 文件中的硬编码值", "reason": "测试文件使用 mock 数据",
                 "file_patterns": ["test_*.py", "*_test.py", "*/tests/*", "*/test/*"]}},
    {"type": "false_positive", "id": "builtin-fp-2",
     "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
     "content": {"pattern": "migrations 文件中的 SQL",
                 "reason": "数据库迁移文件使用原生 SQL 是正常的",
                 "file_patterns": ["*/migrations/*", "*migration*.py"]}},
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_id(record_type: str) -> str:
    ts = datetime.now().strftime("%Y%m%d")
    rand = uuid.uuid4().hex[:4]
    return f"{record_type}-{ts}-{rand}"


def load_all() -> list[dict]:
    """Load all records from review_memory.jsonl. Initialize with builtins if empty."""
    if not MEMORY_FILE.exists():
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _write_all(BUILTIN_RULES)
        return list(BUILTIN_RULES)

    records = []
    with open(MEMORY_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not records:
        _write_all(BUILTIN_RULES)
        return list(BUILTIN_RULES)

    return records


def _write_all(records: list[dict]) -> None:
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _append(record: dict) -> None:
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def get_developer_profile(author: str) -> Optional[dict]:
    """Retrieve a developer profile by GitHub username."""
    records = load_all()
    for r in records:
        if r.get("type") == "developer_profile" and r.get("content", {}).get("author") == author:
            return r
    return None


def update_developer_profile(
    author: str,
    new_issues: Optional[list[dict]] = None,
    strengths: Optional[list[str]] = None,
    growth_areas: Optional[list[str]] = None,
) -> dict:
    """
    Update (or create) a developer profile after a review.
    new_issues: list of {category, severity} dicts from the latest review.
    """
    records = load_all()
    now = _now_iso()

    # Find existing profile
    existing_idx = None
    for i, r in enumerate(records):
        if r.get("type") == "developer_profile" and r.get("content", {}).get("author") == author:
            existing_idx = i
            break

    if existing_idx is not None:
        profile = records[existing_idx]
        content = profile["content"]
    else:
        content = {
            "author": author,
            "issue_history": [],
            "strengths": [],
            "growth_areas": [],
            "pr_count": 0,
            "last_updated": now,
        }
        profile = {
            "type": "developer_profile",
            "id": _new_id("developer_profile"),
            "created_at": now,
            "updated_at": now,
            "content": content,
        }

    # Increment PR count
    content["pr_count"] = content.get("pr_count", 0) + 1
    content["last_updated"] = now

    # Merge issue history
    if new_issues:
        history: dict[str, dict] = {
            item["category"]: item
            for item in content.get("issue_history", [])
        }
        for issue in new_issues:
            cat = issue.get("category", "unknown")
            if cat in history:
                history[cat]["count"] = history[cat].get("count", 0) + 1
                history[cat]["last_seen"] = now
            else:
                history[cat] = {"category": cat, "count": 1, "last_seen": now}
        content["issue_history"] = list(history.values())

    if strengths is not None:
        # Merge without duplicates
        existing = set(content.get("strengths", []))
        content["strengths"] = list(existing | set(strengths))

    if growth_areas is not None:
        existing = set(content.get("growth_areas", []))
        content["growth_areas"] = list(existing | set(growth_areas))

    profile["updated_at"] = now
    profile["content"] = content

    if existing_idx is not None:
        records[existing_idx] = profile
        _write_all(records)
    else:
        _append(profile)

    return profile


def aggregate_repo_patterns(repo: str, issues: list[dict]) -> list[dict]:
    """
    Aggregate issues from a review into repo_pattern records.
    issues: list of {category, severity, file, description} dicts.
    Returns updated/created pattern records.
    """
    records = load_all()
    now = _now_iso()

    # Build index of existing repo_patterns for this repo
    pattern_index: dict[str, int] = {}
    for i, r in enumerate(records):
        if r.get("type") == "repo_pattern" and r.get("content", {}).get("repo") == repo:
            name = r["content"].get("pattern_name", "")
            pattern_index[name] = i

    updated = []
    for issue in issues:
        category = issue.get("category", "unknown")
        severity = issue.get("severity", "low")
        file_path = issue.get("file", "")
        description = issue.get("description", "")

        # Use category+severity as pattern name key
        pattern_name = f"{severity}:{category}"

        if pattern_name in pattern_index:
            idx = pattern_index[pattern_name]
            content = records[idx]["content"]
            content["occurrence_count"] = content.get("occurrence_count", 0) + 1
            content["last_seen"] = now
            # Track affected files
            affected = set(content.get("affected_files", []))
            if file_path:
                affected.add(file_path)
            content["affected_files"] = list(affected)
            # Update trend
            count = content["occurrence_count"]
            content["trend"] = "increasing" if count > 5 else "stable"
            records[idx]["updated_at"] = now
            updated.append(records[idx])
        else:
            new_pattern = {
                "type": "repo_pattern",
                "id": _new_id("repo_pattern"),
                "created_at": now,
                "updated_at": now,
                "content": {
                    "repo": repo,
                    "pattern_name": pattern_name,
                    "category": category,
                    "severity": severity,
                    "occurrence_count": 1,
                    "affected_files": [file_path] if file_path else [],
                    "first_seen": now,
                    "last_seen": now,
                    "trend": "new",
                    "sample_description": description,
                }
            }
            records.append(new_pattern)
            pattern_index[pattern_name] = len(records) - 1
            updated.append(new_pattern)

    _write_all(records)
    return updated


def get_repo_patterns(repo: str) -> list[dict]:
    """Return all repo_pattern records for a given repo."""
    records = load_all()
    return [
        r for r in records
        if r.get("type") == "repo_pattern" and r.get("content", {}).get("repo") == repo
    ]


def get_all_summary(max_tokens: int = 3000) -> str:
    """
    Return formatted rules summary for LLM prompt injection.
    Priority: known_pattern > review_rule > false_positive > fix_template
    Cap at ~max_tokens (approx 4 chars/token).
    """
    records = load_all()
    max_chars = max_tokens * 4

    by_type: dict[str, list] = {
        "known_pattern": [],
        "review_rule": [],
        "false_positive": [],
        "fix_template": [],
    }
    for r in records:
        t = r.get("type", "")
        if t in by_type:
            by_type[t].append(r)

    sections = []

    # known_pattern
    if by_type["known_pattern"]:
        lines = ["## 已知问题模式"]
        for i, r in enumerate(by_type["known_pattern"], 1):
            c = r.get("content", {})
            indicators = ", ".join(c.get("indicators", [])[:4])
            lines.append(f"{i}. [{c.get('severity', '')}] {c.get('name', '')} - 指标: {indicators}")
        sections.append("\n".join(lines))

    # review_rule
    if by_type["review_rule"]:
        lines = ["## 团队代码规范"]
        for i, r in enumerate(by_type["review_rule"], 1):
            c = r.get("content", {})
            lines.append(f"{i}. [{c.get('severity', '')}][{c.get('category', '')}] {c.get('description', '')}")
        sections.append("\n".join(lines))

    # false_positive
    if by_type["false_positive"]:
        lines = ["## 误报排除规则"]
        for i, r in enumerate(by_type["false_positive"], 1):
            c = r.get("content", {})
            patterns = ", ".join(c.get("file_patterns", [])[:3])
            lines.append(f"{i}. {c.get('pattern', '')}（{patterns}）")
        sections.append("\n".join(lines))

    result = "\n\n".join(sections)
    if len(result) > max_chars:
        result = result[:max_chars] + f"\n\n[已截断，共 {len(records)} 条，显示前 {max_chars // 4} tokens]"

    return result


def add_rule(
    description: str,
    severity: str = "warning",
    category: str = "style",
    language: Optional[str] = None,
    example_bad: Optional[str] = None,
    example_good: Optional[str] = None,
    source: str = "manual",
    tags: Optional[list[str]] = None,
) -> dict:
    record = {
        "type": "review_rule",
        "id": _new_id("review_rule"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "content": {
            "description": description,
            "severity": severity,
            "category": category,
            "source": source,
            "tags": tags or [],
        }
    }
    if language:
        record["content"]["language"] = language
    if example_bad:
        record["content"]["example_bad"] = example_bad
    if example_good:
        record["content"]["example_good"] = example_good

    _append(record)
    return record


def add_pattern(name: str, description: str, severity: str = "high",
                indicators: Optional[list[str]] = None) -> dict:
    record = {
        "type": "known_pattern",
        "id": _new_id("known_pattern"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "content": {
            "name": name,
            "description": description,
            "severity": severity,
            "indicators": indicators or [],
        }
    }
    _append(record)
    return record


def add_false_positive(pattern: str, reason: str,
                       file_patterns: Optional[list[str]] = None) -> dict:
    record = {
        "type": "false_positive",
        "id": _new_id("false_positive"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "content": {
            "pattern": pattern,
            "reason": reason,
            "file_patterns": file_patterns or [],
        }
    }
    _append(record)
    return record


# ─── Tool definitions for AgentRunner ─────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "memory_get_all",
        "description": "Get all review rules, known patterns, and false positives as a formatted summary",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_tokens": {
                    "type": "integer",
                    "description": "Maximum tokens for the summary (default 3000)",
                }
            },
        },
    },
    {
        "name": "memory_add_pattern",
        "description": "Add a new known issue pattern to the review memory",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "indicators": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name", "description"],
        },
    },
    {
        "name": "memory_add_false_positive",
        "description": "Mark a pattern as a false positive in certain file contexts",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "reason": {"type": "string"},
                "file_patterns": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["pattern", "reason"],
        },
    },
    {
        "name": "memory_get_developer_profile",
        "description": "Get the developer growth profile for a GitHub user, including issue history, strengths, and growth areas",
        "input_schema": {
            "type": "object",
            "properties": {
                "author": {"type": "string", "description": "GitHub username of the developer"},
            },
            "required": ["author"],
        },
    },
    {
        "name": "memory_update_developer_profile",
        "description": "Update the developer profile after a review, recording new issues found, strengths, and growth areas",
        "input_schema": {
            "type": "object",
            "properties": {
                "author": {"type": "string", "description": "GitHub username"},
                "new_issues": {
                    "type": "array",
                    "description": "Issues found in this review",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string"},
                            "severity": {"type": "string"},
                        },
                    },
                },
                "strengths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Positive patterns observed",
                },
                "growth_areas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Areas needing improvement",
                },
            },
            "required": ["author"],
        },
    },
    {
        "name": "memory_aggregate_patterns",
        "description": "Aggregate issues from a review into cross-PR repo pattern statistics",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository in owner/repo format"},
                "issues": {
                    "type": "array",
                    "description": "Issues found in this review",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string"},
                            "severity": {"type": "string"},
                            "file": {"type": "string"},
                            "description": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["repo", "issues"],
        },
    },
]


class MemoryTools:
    """Tool handler for memory operations."""

    def memory_get_all(self, max_tokens: int = 3000) -> str:
        return get_all_summary(max_tokens=max_tokens)

    def memory_add_pattern(self, name: str, description: str,
                           severity: str = "high",
                           indicators: Optional[list[str]] = None) -> dict:
        record = add_pattern(name, description, severity, indicators)
        return {"status": "ok", "id": record["id"]}

    def memory_add_false_positive(self, pattern: str, reason: str,
                                  file_patterns: Optional[list[str]] = None) -> dict:
        record = add_false_positive(pattern, reason, file_patterns)
        return {"status": "ok", "id": record["id"]}

    def memory_get_developer_profile(self, author: str) -> dict:
        profile = get_developer_profile(author)
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
                "message": "No profile yet — this is the developer's first review",
            }
        }

    def memory_update_developer_profile(
        self,
        author: str,
        new_issues: Optional[list[dict]] = None,
        strengths: Optional[list[str]] = None,
        growth_areas: Optional[list[str]] = None,
    ) -> dict:
        record = update_developer_profile(author, new_issues, strengths, growth_areas)
        return {"status": "ok", "id": record["id"], "pr_count": record["content"]["pr_count"]}

    def memory_aggregate_patterns(self, repo: str, issues: list[dict]) -> dict:
        updated = aggregate_repo_patterns(repo, issues)
        return {"status": "ok", "patterns_updated": len(updated), "repo": repo}
