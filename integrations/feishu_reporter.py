"""
Feishu (Lark) integration for Code Review Agent.

Pushes PR review summaries, developer profiles, and health reports to
Feishu group chats via the lark-cli tool.

Prerequisites:
  1. Install lark-cli:  npx @larksuite/cli
  2. Configure auth:   lark-cli config init
  3. Create a Feishu app with im:message:send_as_bot permission
  4. Add the bot to the target group chat

Env vars:
  FEISHU_CHAT_ID   — target group chat ID (oc_xxx)
  FEISHU_ENABLED   — set to "1" or "true" to enable (default: disabled)
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import Config


def _sanitize_markdown(text: str) -> str:
    """Clean markdown content to avoid Feishu post-format conversion issues.

    Feishu's markdown-to-post converter treats certain line patterns as
    section terminators, dropping all subsequent content. We remove these.
    """
    import re

    # Remove horizontal-rule lines entirely (---, ***, ___, or em-dash separators)
    # These cause Feishu's post converter to truncate the rest of the message
    text = re.sub(r'^\s*(\-{3,}|\*{3,}|_{3,}|—{1,})\s*$', '', text, flags=re.MULTILINE)

    # Collapse 3+ consecutive blank lines into 2
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text


def _find_lark_cli() -> Optional[list[str]]:
    """Locate the lark-cli and return the base command list.

    On Windows, .cmd wrappers use cmd.exe which truncates arguments containing
    special characters (#, |, etc.). We bypass this by calling node + run.js
    directly when possible.

    Returns:
        A list like ["node", "path/to/run.js"] or ["lark-cli"] or None.
    """
    # Check explicit path from env
    explicit = os.environ.get("LARK_CLI_PATH", "")
    if explicit and Path(explicit).exists():
        return [explicit]

    if sys.platform == "win32":
        # Strategy: find lark-cli.cmd → extract the run.js path → call node directly
        try:
            result = subprocess.run(
                ["where", "lark-cli"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                paths = result.stdout.strip().split("\n")
                for p in paths:
                    p = p.strip()
                    if p.endswith(".cmd") and Path(p).exists():
                        # Derive run.js path from .cmd location
                        # .cmd is at: <dir>/lark-cli.cmd
                        # run.js is at: <dir>/node_modules/@larksuite/cli/scripts/run.js
                        cmd_dir = Path(p).parent
                        run_js = cmd_dir / "node_modules" / "@larksuite" / "cli" / "scripts" / "run.js"
                        if run_js.exists():
                            return ["node", str(run_js)]
                        # Fallback: use .cmd (may truncate special chars)
                        return [p]
        except Exception:
            pass

        # Fallback: npx
        try:
            result = subprocess.run(
                ["where", "npx"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                return ["npx", "@larksuite/cli"]
        except Exception:
            pass
    else:
        # Unix: use which
        try:
            result = subprocess.run(
                ["which", "lark-cli"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                path = result.stdout.strip().split("\n")[0].strip()
                if Path(path).exists():
                    return [path]
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["which", "npx"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                return ["npx", "@larksuite/cli"]
        except Exception:
            pass

    return None


def _run_lark_cli(args: list[str], timeout: int = 30) -> dict:
    """Run a lark-cli command and return parsed JSON result.

    Returns:
        {"ok": True, "data": ...} or {"ok": False, "error": "..."}
    """
    base_cmd = _find_lark_cli()

    if base_cmd is None:
        return {"ok": False, "error": "lark-cli not found. Install with: npx @larksuite/cli"}

    cmd = base_cmd + args

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "NO_COLOR": "1"},
        )

        if result.returncode != 0:
            stderr = result.stderr.strip() if result.stderr else "unknown error"
            # Try to parse structured error from lark-cli
            try:
                err_data = json.loads(result.stdout or result.stderr or "{}")
                hint = err_data.get("hint", err_data.get("message", stderr))
                return {"ok": False, "error": hint}
            except json.JSONDecodeError:
                return {"ok": False, "error": stderr}

        # Parse JSON envelope from stdout
        stdout = result.stdout.strip()
        if not stdout:
            return {"ok": True, "data": {}}

        try:
            envelope = json.loads(stdout)
            if isinstance(envelope, dict):
                ok = envelope.get("ok", True)
                if not ok:
                    return {"ok": False, "error": envelope.get("error", str(envelope))}
                return {"ok": True, "data": envelope.get("data", envelope)}
            return {"ok": True, "data": envelope}
        except json.JSONDecodeError:
            return {"ok": True, "data": stdout}

    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"lark-cli timed out after {timeout}s"}
    except FileNotFoundError:
        return {"ok": False, "error": "lark-cli not found. Install with: npx @larksuite/cli"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@dataclass
class FeishuConfig:
    """Feishu integration settings."""
    enabled: bool = False
    chat_id: str = ""
    bot_name: str = "Code Review Agent"

    @classmethod
    def from_env(cls) -> "FeishuConfig":
        enabled = os.environ.get("FEISHU_ENABLED", "").lower() in ("1", "true", "yes")
        return cls(
            enabled=enabled,
            chat_id=os.environ.get("FEISHU_CHAT_ID", ""),
            bot_name=os.environ.get("FEISHU_BOT_NAME", "Code Review Agent"),
        )


class FeishuReporter:
    """Sends code review results to Feishu group chats via lark-cli."""

    def __init__(self, config: Optional[FeishuConfig] = None):
        self.config = config or FeishuConfig.from_env()

    @property
    def available(self) -> bool:
        """Check if Feishu integration is properly configured and ready."""
        if not self.config.enabled:
            return False
        if not self.config.chat_id:
            return False
        if _find_lark_cli() is None:
            return False
        return True

    def send_text(self, text: str, chat_id: str = "") -> dict:
        """Send a plain text message to a Feishu chat."""
        target = chat_id or self.config.chat_id
        if not target:
            return {"ok": False, "error": "No chat_id configured"}

        return _run_lark_cli([
            "im", "+messages-send",
            "--chat-id", target,
            "--text", text,
        ])

    def send_markdown(self, markdown: str, chat_id: str = "") -> dict:
        """Send a markdown message (rendered as rich post in Feishu)."""
        target = chat_id or self.config.chat_id
        if not target:
            return {"ok": False, "error": "No chat_id configured"}

        return _run_lark_cli([
            "im", "+messages-send",
            "--chat-id", target,
            "--markdown", markdown,
        ])

    def send_review_summary(
        self,
        pr_label: str,
        decision: str,
        issues_found: int,
        session_id: str,
        stats: dict,
        multi_agent: Optional[dict] = None,
        pr_title: str = "",
        pr_url: str = "",
    ) -> dict:
        """Send a formatted PR review summary to Feishu.

        Builds a rich markdown message with decision badge, severity breakdown,
        per-role findings, and recommendations.
        """
        if not self.available:
            return {"ok": False, "error": "Feishu not configured or lark-cli not found"}

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Decision badge
        decision_emoji = {
            "APPROVE": "✅",
            "REQUEST_CHANGES": "🔴",
            "COMMENT": "💬",
        }
        emoji = decision_emoji.get(decision, "📝")

        # Build the message
        lines = [
            f"## {emoji} Code Review: {pr_label}",
            "",
        ]

        if pr_title:
            lines.append(f"**PR**: [{pr_title}]({pr_url})" if pr_url else f"**PR**: {pr_title}")
        else:
            lines.append(f"**PR**: {pr_url}" if pr_url else f"**PR**: {pr_label}")

        lines += [
            "",
            f"**Decision**: {decision}",
            f"**Issues Found**: {issues_found}",
            f"**Time**: {now_str}",
            f"**Session**: `{session_id}`",
            "",
        ]

        # Severity breakdown
        severity_counts = stats.get("severity_counts", {})
        if severity_counts:
            lines.append("### Severity Breakdown")
            lines.append("")
            sev_icons = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢", "info": "🔵"}
            for sev in ("critical", "high", "medium", "low", "info"):
                count = severity_counts.get(sev, 0)
                if count > 0:
                    lines.append(f"- {sev_icons.get(sev, '⚪')} **{sev.title()}**: {count}")
            lines.append("")

        # Multi-agent details
        if multi_agent:
            per_role = multi_agent.get("per_role", {})
            if per_role:
                lines.append("### Per-Role Analysis")
                lines.append("")
                for role, info in per_role.items():
                    status = info.get("status", "?")
                    conf = info.get("confidence", "?")
                    findings = info.get("finding_count", 0)
                    status_icon = "✓" if status == "ok" else "✗"
                    lines.append(
                        f"- **{role.title()}** {status_icon}: "
                        f"{findings} findings (confidence: {conf})"
                    )
                lines.append("")

            # Key findings (top 5)
            findings = multi_agent.get("findings", [])
            if findings:
                lines.append("### Key Findings")
                lines.append("")
                # Sort by severity priority
                sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
                sorted_findings = sorted(
                    findings,
                    key=lambda f: sev_order.get(f.get("severity", "low"), 99),
                )[:5]
                for i, f in enumerate(sorted_findings, 1):
                    sev = f.get("severity", "low")
                    file_path = f.get("file", "?")
                    line = f.get("line", "")
                    desc = f.get("description", "")
                    loc = f"{file_path}:{line}" if line else file_path
                    lines.append(f"{i}. {sev_icons.get(sev, '⚪')} **[{sev.upper()}]** `{loc}` — {desc[:120]}")
                lines.append("")

            # Recommendations
            recs = multi_agent.get("recommendations", [])
            if recs:
                lines.append("### Recommendations")
                lines.append("")
                for r in recs[:3]:
                    lines.append(f"- {r}")
                lines.append("")

            # Metrics
            total_dur = stats.get("total_duration_ms", 0)
            total_tokens = stats.get("total_tokens", 0)
            if total_dur or total_tokens:
                lines.append("### Metrics")
                lines.append("")
                if total_dur:
                    lines.append(f"- **Duration**: {total_dur / 1000:.1f}s")
                if total_tokens:
                    lines.append(f"- **Tokens**: {total_tokens:,}")
                workers = stats.get("workers_total", 0)
                if workers:
                    lines.append(f"- **Workers**: {workers}")
                lines.append("")

        lines.append("---")
        lines.append(f"*Generated by {self.config.bot_name} | {now_str}*")

        return self.send_markdown("\n".join(lines))

    def send_developer_profile(self, author: str) -> dict:
        """Fetch and send a developer profile summary to Feishu."""
        if not self.available:
            return {"ok": False, "error": "Feishu not configured or lark-cli not found"}

        from ..tools.memory_tools import get_developer_profile

        profile = get_developer_profile(author)

        if not profile:
            return self.send_text(f"📋 No review history found for developer **{author}**.")

        content = profile.get("content", {})
        pr_count = content.get("pr_count", 0)
        issue_history = content.get("issue_history", [])
        strengths = content.get("strengths", [])
        growth_areas = content.get("growth_areas", [])
        last_updated = content.get("last_updated", "")[:10]

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        lines = [
            f"## 👤 Developer Profile: {author}",
            "",
            f"**PRs Reviewed**: {pr_count}",
            f"**Last Updated**: {last_updated}",
            f"**Report Time**: {now_str}",
            "",
        ]

        if strengths:
            lines.append("### ✅ Strengths")
            lines.append("")
            for s in strengths:
                lines.append(f"- {s}")
            lines.append("")

        if growth_areas:
            lines.append("### 🌱 Growth Areas")
            lines.append("")
            for g in growth_areas:
                lines.append(f"- {g}")
            lines.append("")

        if issue_history:
            lines.append("### 📊 Issue History")
            lines.append("")
            # Sort by count descending
            sorted_issues = sorted(
                issue_history,
                key=lambda x: x.get("count", 0),
                reverse=True,
            )
            for issue in sorted_issues:
                cat = issue.get("category", "unknown")
                count = issue.get("count", 0)
                last = issue.get("last_seen", "")[:10]
                lines.append(f"- **{cat}**: {count} occurrences (last: {last})")
            lines.append("")

        lines.append("---")
        lines.append(f"*Generated by {self.config.bot_name}*")

        return self.send_markdown("\n".join(lines))

    def send_health_report(self, repo: str, report: str) -> dict:
        """Send a repository health report to Feishu.

        Since health reports can be long, this splits into multiple messages
        if needed, or uploads as a file for very large reports.
        """
        if not self.available:
            return {"ok": False, "error": "Feishu not configured or lark-cli not found"}

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        header = f"## 📊 Health Report: {repo}\n\n*Generated: {now_str}*\n\n"

        # If report is short enough, send as markdown
        if len(report) < 8000:
            return self.send_markdown(header + report)

        # For long reports, send summary header + truncated content
        # Truncate at ~7000 chars to stay within API limits
        truncated = report[:7000] + "\n\n---\n*Report truncated due to length. Run `health-report --repo {repo}` for full output.*"
        return self.send_markdown(header + truncated)

    def send_simple_result(
        self,
        pr_label: str,
        decision: str,
        issues_found: int,
        review_text: str = "",
    ) -> dict:
        """Send a review result with full LLM analysis for single-agent mode.

        Unlike multi-agent mode, the LLM output already contains rich markdown
        (headings, tables, etc.). We send it as the main content with only a
        short prefix to avoid nested-markdown corruption.
        """
        if not self.available:
            return {"ok": False, "error": "Feishu not configured or lark-cli not found"}

        decision_emoji = {
            "APPROVE": "✅",
            "REQUEST_CHANGES": "🔴",
            "COMMENT": "💬",
        }
        emoji = decision_emoji.get(decision, "📝")
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Build a simple prefix that won't conflict with LLM's own markdown
        prefix = f"{emoji} **Code Review | {pr_label} | Decision: {decision}**\n\n"

        if review_text:
            # Sanitize to avoid Feishu post-format truncation (e.g. --- lines)
            review_text = _sanitize_markdown(review_text)
            max_len = 6000
            if len(review_text) > max_len:
                review_text = review_text[:max_len] + "\n\n*(Review text truncated due to length)*"
        else:
            review_text = f"Issues found: {issues_found}\nTime: {now_str}"

        msg = prefix + review_text
        return self.send_markdown(msg)


# Module-level convenience function
def notify_review_result(
    pr_label: str,
    decision: str,
    issues_found: int,
    session_id: str,
    stats: dict,
    multi_agent: Optional[dict] = None,
    pr_title: str = "",
    pr_url: str = "",
    review_text: str = "",
) -> dict:
    """Convenience function to send review result to Feishu if configured."""
    reporter = FeishuReporter()
    if not reporter.available:
        return {"ok": False, "error": "Feishu not available (set FEISHU_ENABLED=true and FEISHU_CHAT_ID)"}

    if multi_agent:
        return reporter.send_review_summary(
            pr_label=pr_label,
            decision=decision,
            issues_found=issues_found,
            session_id=session_id,
            stats=stats,
            multi_agent=multi_agent,
            pr_title=pr_title,
            pr_url=pr_url,
        )
    else:
        return reporter.send_simple_result(
            pr_label=pr_label,
            decision=decision,
            issues_found=issues_found,
            review_text=review_text,
        )
