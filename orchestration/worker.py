"""
WorkerAgent: one specialised reviewer per role (security / performance /
architecture / style).

Each Worker is a thin shell around a *bounded* AgentRunner (10 iterations
instead of 30) plus a role-specific system-prompt suffix and a tool subset
exposed via a ScopedToolRouter wrapper. Workers do **not** share message
history — every Worker.run() builds its own conversation from scratch.
"""
import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from ..agent.llm_client import LLMClient
from ..agent.runner import AgentRunner
from ..agent.skill_loader import SkillLoader
from ..agent.tool_router import ToolRouter
from .schemas import (
    Confidence,
    Finding,
    SubTask,
    WorkerResult,
    WorkerRole,
)

# ─── Role-specific focus instructions ─────────────────────────────────────────

ROLE_FOCUS: dict[str, str] = {
    "security": (
        "你的专注领域是 **安全审查**，只汇报安全相关问题：\n"
        "1. 输入校验、边界检查、SSRF/XXE\n"
        "2. SQL/命令/模板注入、XSS、反序列化漏洞\n"
        "3. 认证、授权、会话管理、CSRF\n"
        "4. 硬编码密钥/凭证、敏感信息日志泄漏\n"
        "5. 弱加密、不安全随机数、TLS 配置\n"
        "忽略风格、性能、命名等无关问题。"
    ),
    "performance": (
        "你的专注领域是 **性能审查**，只汇报性能相关问题：\n"
        "1. 时间/空间复杂度劣化（N+1 查询、嵌套循环、不必要的 O(n²)）\n"
        "2. 同步阻塞 I/O、错误的并发模型、锁粒度\n"
        "3. 内存泄漏、不必要的拷贝、大对象常驻\n"
        "4. 缓存缺失或失效策略错误\n"
        "5. 数据库索引、批处理、连接池\n"
        "忽略安全、风格、文档等无关问题。"
    ),
    "architecture": (
        "你的专注领域是 **架构审查**，只汇报设计与结构问题：\n"
        "1. 分层、边界、循环依赖、错放的职责\n"
        "2. 抽象泄漏、上帝类、过度耦合\n"
        "3. 错误处理与日志的横切关注点\n"
        "4. 与既有模式/约定的偏离\n"
        "5. 可测试性与扩展性\n"
        "忽略具体语法风格与微观性能。"
    ),
    "style": (
        "你的专注领域是 **代码风格**，只汇报风格与可读性问题：\n"
        "1. 命名（变量、函数、类）的清晰度与一致性\n"
        "2. 注释/文档字符串的必要性与准确性\n"
        "3. 死代码、重复代码、过长函数\n"
        "4. 格式化、行宽、空白\n"
        "5. 与团队既定 lint 规则的偏离\n"
        "忽略安全、性能、架构等高层问题。"
    ),
}

# ─── Tool subsets ─────────────────────────────────────────────────────────────

# All review-only Workers share the read tool set — write tools belong to the
# Leader (final synthesis stage) and are filtered out here.
_READ_TOOLS: frozenset[str] = frozenset({
    "get_pull_request",
    "get_pull_request_files",
    "list_commits",
    "get_file_contents",
    "get_pull_request_status",
    "detect_test_framework",
    "memory_get_all",
    "memory_get_developer_profile",
})

ROLE_TOOLS: dict[str, frozenset[str]] = {
    "security": _READ_TOOLS,
    "performance": _READ_TOOLS,
    "architecture": _READ_TOOLS,
    "style": _READ_TOOLS,
}


class ScopedToolRouter:
    """Duck-type wrapper around ToolRouter exposing only an allowed subset."""

    def __init__(self, base: ToolRouter, allowed: frozenset[str]):
        self._base = base
        self._allowed = allowed

    @property
    def tool_definitions(self) -> list[dict]:
        return [t for t in self._base.tool_definitions if t["name"] in self._allowed]

    def execute(self, tool_name: str, tool_input: dict) -> Any:
        if tool_name not in self._allowed:
            return {"error": f"tool '{tool_name}' not authorized for this worker"}
        return self._base.execute(tool_name, tool_input)


class _BoundedWorkerRunner(AgentRunner):
    """AgentRunner restricted to 10 iterations per Worker subtask."""

    MAX_ITERATIONS = 6


class WorkerAgent:
    """A single role-scoped reviewer Agent.

    Internally composes a bounded AgentRunner with a scoped ToolRouter and
    a role-augmented system prompt. Worker.run() is synchronous; the
    TaskScheduler wraps it in asyncio.to_thread.
    """

    def __init__(
        self,
        role: WorkerRole,
        llm_client: LLMClient,
        tool_router: ToolRouter,
        skill_loader: SkillLoader,
        trajectories_dir: Path,
    ):
        self.role = role
        scoped = ScopedToolRouter(tool_router, ROLE_TOOLS[role])
        # Reuse code-review skill chain; the bounded runner subclass enforces 10 iters
        runner = _BoundedWorkerRunner(
            llm_client=llm_client,
            tool_router=scoped,            # type: ignore[arg-type]
            skill_loader=skill_loader,
            trajectories_dir=trajectories_dir,
            skill_name="code-review",
        )
        # Append role focus to the loaded system prompt (no AgentRunner edits)
        runner.system_prompt = (
            f"{runner.system_prompt}\n\n## 角色专注 ({role})\n{ROLE_FOCUS[role]}\n\n"
            "**输出约定**：在最终回复末尾追加一个 ```json 代码块，结构为 "
            '{"findings": [{"file":"...","line":N,"severity":"...",'
            '"category":"...","description":"..."}]}。'
        )
        self._runner = runner

    # ── Public API ─────────────────────────────────────────────────────────

    def run(self, subtask: SubTask) -> WorkerResult:
        """Execute the subtask; convert AgentResult → WorkerResult."""
        started = time.time()
        task_text = self._build_task_text(subtask)
        try:
            agent_result = self._runner.run(
                task=task_text,
                pr=f"{subtask.context.get('owner','?')}/{subtask.context.get('repo','?')}#"
                   f"{subtask.context.get('pull_number','?')}/{self.role}",
                extra_context={
                    "subtask_id": subtask.id,
                    "role": self.role,
                    "files": [f.get("filename") for f in subtask.files],
                },
            )
        except Exception as e:
            return WorkerResult(
                subtask_id=subtask.id, role=self.role, status="failed", error=str(e),
                duration_ms=int((time.time() - started) * 1000),
            )

        stats = agent_result.stats or {}
        findings = _parse_findings(agent_result.final_response, default_file_hint=
                                   subtask.files[0]["filename"] if subtask.files else "")
        return WorkerResult(
            subtask_id=subtask.id,
            role=self.role,
            findings=findings,
            confidence=_estimate_confidence(stats, findings),
            token_usage={
                "input": stats.get("total_input_tokens", 0),
                "output": stats.get("total_output_tokens", 0),
            },
            raw_text=agent_result.final_response,
            session_id=agent_result.session_id,
            iterations=stats.get("iterations", 0),
            duration_ms=stats.get("duration_ms", int((time.time() - started) * 1000)),
            status="ok",
        )

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _build_task_text(self, subtask: SubTask) -> str:
        ctx = subtask.context
        target = f"{ctx.get('owner')}/{ctx.get('repo')} #{ctx.get('pull_number')}"
        files = "\n".join(f"- {f.get('filename')} (+{f.get('additions',0)}/"
                          f"-{f.get('deletions',0)})" for f in subtask.files) or "(无)"
        return (
            f"作为 **{self.role}** 审查员，审查 {target}。\n\n"
            f"重点关注以下文件：\n{files}\n\n"
            "请按角色专注范围分析，并以 JSON findings 块结尾。"
        )


# ─── Module-level parsers ─────────────────────────────────────────────────────

_JSON_FINDINGS_RE = re.compile(r"```json\s*(\{[\s\S]*?\})\s*```")


def _parse_findings(text: str, default_file_hint: str = "") -> list[Finding]:
    """Best-effort extraction of structured findings from the Worker's final
    text. Falls back to an empty list when no JSON block is present."""
    if not text:
        return []
    for match in _JSON_FINDINGS_RE.finditer(text):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        items = payload.get("findings") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            continue
        out: list[Finding] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            out.append(Finding(
                file=str(it.get("file") or default_file_hint),
                line=it.get("line") if isinstance(it.get("line"), int) else None,
                severity=str(it.get("severity") or "medium").lower(),  # type: ignore[arg-type]
                category=str(it.get("category") or "general"),
                description=str(it.get("description") or "").strip(),
            ))
        return out
    return []


def _estimate_confidence(stats: dict, findings: list[Finding]) -> Confidence:
    iters = stats.get("iterations", 0)
    tools = stats.get("tool_calls", 0)
    if iters >= 3 and tools >= 1 and findings:
        return "high"
    if iters == 0 or stats.get("duration_ms", 0) == 0:
        return "low"
    return "medium"
