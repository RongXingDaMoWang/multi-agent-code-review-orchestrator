"""
Trajectory data structure definitions.
Compatible with Claude Code's JSONL conversation format.
"""
from dataclasses import dataclass, field
from typing import Any, Optional
import uuid
import time


def new_uuid() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


# ─── Content block types ────────────────────────────────────────────────────

@dataclass
class ThinkingBlock:
    thinking: str
    type: str = "thinking"

    def to_dict(self) -> dict:
        return {"type": self.type, "thinking": self.thinking}


@dataclass
class TextBlock:
    text: str
    type: str = "text"

    def to_dict(self) -> dict:
        return {"type": self.type, "text": self.text}


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"

    def to_dict(self) -> dict:
        return {"type": self.type, "id": self.id, "name": self.name, "input": self.input}


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: list[dict]  # [{"type": "text", "text": "..."}]
    type: str = "tool_result"

    def to_dict(self) -> dict:
        return {"type": self.type, "tool_use_id": self.tool_use_id, "content": self.content}


# ─── LLM Response ────────────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    content: list[dict]           # Raw content blocks from Anthropic API
    stop_reason: str              # "end_turn" | "tool_use"
    usage: dict                   # {"input_tokens": N, "output_tokens": N}
    model: str = ""

    @property
    def text(self) -> str:
        """Extract concatenated text from content blocks."""
        parts = []
        for block in self.content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block["text"])
        return "\n".join(parts)

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        """Extract tool_use blocks."""
        result = []
        for block in self.content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                result.append(ToolUseBlock(
                    id=block["id"],
                    name=block["name"],
                    input=block["input"]
                ))
        return result


# ─── Trajectory records ──────────────────────────────────────────────────────

@dataclass
class SessionStartRecord:
    session_id: str
    pr: str                       # "owner/repo#42"
    timestamp: str = field(default_factory=now_iso)
    type: str = "session_start"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "pr": self.pr,
            "timestamp": self.timestamp,
        }


@dataclass
class UserRecord:
    content: Any                  # str or list of content blocks
    uuid: str = field(default_factory=new_uuid)
    parent_uuid: Optional[str] = None
    type: str = "user"

    def to_dict(self) -> dict:
        d = {
            "type": self.type,
            "uuid": self.uuid,
            "message": {"role": "user", "content": self.content},
        }
        if self.parent_uuid:
            d["parentUuid"] = self.parent_uuid
        return d


@dataclass
class AssistantRecord:
    content: list[dict]           # thinking + text + tool_use blocks
    stop_reason: str
    usage: dict
    uuid: str = field(default_factory=new_uuid)
    parent_uuid: Optional[str] = None
    type: str = "assistant"

    def to_dict(self) -> dict:
        d = {
            "type": self.type,
            "uuid": self.uuid,
            "message": {
                "role": "assistant",
                "content": self.content,
                "stop_reason": self.stop_reason,
                "usage": self.usage,
            },
        }
        if self.parent_uuid:
            d["parentUuid"] = self.parent_uuid
        return d


@dataclass
class SessionEndRecord:
    session_id: str
    stats: dict
    timestamp: str = field(default_factory=now_iso)
    type: str = "session_end"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "stats": self.stats,
        }


# ─── Agent result ─────────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    messages: list[dict]
    final_response: str
    session_id: str
    stats: dict = field(default_factory=dict)


# ─── Orchestration records (Phase 3) ──────────────────────────────────────────
# Emitted by the multi-agent orchestrator and appended to the same JSONL stream.
# Coexist with single-agent session_start/assistant/user/session_end records.

@dataclass
class OrchestrationStartRecord:
    task_id: str
    pr_url: str
    mode: str                           # "multi" | future modes
    timestamp: str = field(default_factory=now_iso)
    type: str = "orchestration_start"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "task_id": self.task_id,
            "pr_url": self.pr_url,
            "mode": self.mode,
            "timestamp": self.timestamp,
        }


@dataclass
class SubTaskDispatchedRecord:
    task_id: str
    subtask_id: str
    role: str                           # security/performance/architecture/style
    worker_id: str
    priority: int = 50
    file_count: int = 0
    timestamp: str = field(default_factory=now_iso)
    type: str = "subtask_dispatched"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "task_id": self.task_id,
            "subtask_id": self.subtask_id,
            "role": self.role,
            "worker_id": self.worker_id,
            "priority": self.priority,
            "file_count": self.file_count,
            "timestamp": self.timestamp,
        }


@dataclass
class WorkerCompletedRecord:
    task_id: str
    subtask_id: str
    role: str
    duration_ms: int
    token_usage: dict                   # {"input": N, "output": N}
    findings_count: int
    status: str                         # "ok" | "failed" | "timeout"
    confidence: str = "medium"
    session_id: str = ""
    timestamp: str = field(default_factory=now_iso)
    type: str = "worker_completed"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "task_id": self.task_id,
            "subtask_id": self.subtask_id,
            "role": self.role,
            "duration_ms": self.duration_ms,
            "token_usage": self.token_usage,
            "findings_count": self.findings_count,
            "status": self.status,
            "confidence": self.confidence,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
        }


@dataclass
class ReviewerVerdictRecord:
    task_id: str
    accepted_count: int
    rejected_count: int
    retry_count: int
    conflicts_count: int = 0
    timestamp: str = field(default_factory=now_iso)
    type: str = "reviewer_verdict"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "task_id": self.task_id,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "retry_count": self.retry_count,
            "conflicts_count": self.conflicts_count,
            "timestamp": self.timestamp,
        }


@dataclass
class OrchestrationEndRecord:
    task_id: str
    total_duration_ms: int
    total_tokens: dict                  # {"input": N, "output": N}
    final_severity_counts: dict         # {"critical": 0, "high": 1, ...}
    decision: str = ""
    timestamp: str = field(default_factory=now_iso)
    type: str = "orchestration_end"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "task_id": self.task_id,
            "total_duration_ms": self.total_duration_ms,
            "total_tokens": self.total_tokens,
            "final_severity_counts": self.final_severity_counts,
            "decision": self.decision,
            "timestamp": self.timestamp,
        }
