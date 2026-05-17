"""
Real-time trajectory recorder.
Writes each step immediately to JSONL file.
"""
import json
import time
from pathlib import Path
from typing import Any, Optional

from .schemas import (
    SessionStartRecord, SessionEndRecord,
    UserRecord, AssistantRecord,
    LLMResponse, ToolUseBlock,
    OrchestrationStartRecord, SubTaskDispatchedRecord,
    WorkerCompletedRecord, ReviewerVerdictRecord, OrchestrationEndRecord,
    new_uuid, now_iso,
)


class TrajectoryLogger:
    def __init__(self, output_dir: Path, session_id: str, pr: str, prefix: str = "trace"):
        output_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self.pr = pr
        self.output_path = output_dir / f"{prefix}_{session_id}.jsonl"
        self._file = open(self.output_path, "w", encoding="utf-8")
        self._last_uuid: Optional[str] = None

        # Stats tracking
        self._start_time = time.time()
        self._llm_calls = 0
        self._tool_calls = 0
        self._total_input_tokens = 0
        self._total_output_tokens = 0

        # Write session start
        self._write(SessionStartRecord(
            session_id=session_id,
            pr=pr,
        ).to_dict())

    def log_user_input(self, content: str) -> str:
        """Log the initial user task message."""
        record = UserRecord(content=content, parent_uuid=self._last_uuid)
        self._write(record.to_dict())
        self._last_uuid = record.uuid
        return record.uuid

    def log_assistant(self, response: LLMResponse) -> str:
        """Log assistant response (thinking + text + tool_use blocks)."""
        self._llm_calls += 1
        self._total_input_tokens += response.usage.get("input_tokens", 0)
        self._total_output_tokens += response.usage.get("output_tokens", 0)

        record = AssistantRecord(
            content=response.content,
            stop_reason=response.stop_reason,
            usage=response.usage,
            parent_uuid=self._last_uuid,
        )
        self._write(record.to_dict())
        self._last_uuid = record.uuid
        return record.uuid

    def log_tool_result(self, tool_use: ToolUseBlock, result: Any) -> str:
        """Log a tool result as a user message."""
        self._tool_calls += 1

        # Normalize result to string
        if isinstance(result, (dict, list)):
            result_text = json.dumps(result, ensure_ascii=False)
        else:
            result_text = str(result)

        tool_result_content = [
            {"type": "tool_result", "tool_use_id": tool_use.id,
             "content": [{"type": "text", "text": result_text}]}
        ]
        record = UserRecord(content=tool_result_content, parent_uuid=self._last_uuid)
        self._write(record.to_dict())
        self._last_uuid = record.uuid
        return record.uuid

    # ── Orchestration-level events (Phase 3) ─────────────────────────────
    # Emitted by the multi-agent orchestrator. Coexist with the per-Agent
    # records above; do not affect _llm_calls / _tool_calls counters.

    def log_orchestration_start(
        self, task_id: str, pr_url: str, mode: str = "multi"
    ) -> None:
        """Record the beginning of a multi-agent orchestration run."""
        self._write(OrchestrationStartRecord(
            task_id=task_id, pr_url=pr_url, mode=mode,
        ).to_dict())

    def log_subtask_dispatched(
        self,
        task_id: str,
        subtask_id: str,
        role: str,
        worker_id: str,
        priority: int = 50,
        file_count: int = 0,
    ) -> None:
        """Record a SubTask being assigned to a Worker."""
        self._write(SubTaskDispatchedRecord(
            task_id=task_id, subtask_id=subtask_id, role=role,
            worker_id=worker_id, priority=priority, file_count=file_count,
        ).to_dict())

    def log_worker_completed(
        self,
        task_id: str,
        subtask_id: str,
        role: str,
        duration_ms: int,
        token_usage: dict,
        findings_count: int,
        status: str = "ok",
        confidence: str = "medium",
        session_id: str = "",
    ) -> None:
        """Record a Worker finishing (success, failure, or timeout)."""
        self._write(WorkerCompletedRecord(
            task_id=task_id, subtask_id=subtask_id, role=role,
            duration_ms=duration_ms, token_usage=token_usage,
            findings_count=findings_count, status=status,
            confidence=confidence, session_id=session_id,
        ).to_dict())

    def log_reviewer_verdict(
        self,
        task_id: str,
        accepted_count: int,
        rejected_count: int,
        retry_count: int,
        conflicts_count: int = 0,
    ) -> None:
        """Record the Reviewer's verdict over the Worker batch."""
        self._write(ReviewerVerdictRecord(
            task_id=task_id, accepted_count=accepted_count,
            rejected_count=rejected_count, retry_count=retry_count,
            conflicts_count=conflicts_count,
        ).to_dict())

    def log_orchestration_end(
        self,
        task_id: str,
        total_duration_ms: int,
        total_tokens: dict,
        final_severity_counts: dict,
        decision: str = "",
    ) -> None:
        """Record the end of a multi-agent orchestration run."""
        self._write(OrchestrationEndRecord(
            task_id=task_id, total_duration_ms=total_duration_ms,
            total_tokens=total_tokens,
            final_severity_counts=final_severity_counts,
            decision=decision,
        ).to_dict())

    def close(self, extra_stats: Optional[dict] = None) -> dict:
        """Write session_end and close file. Returns final stats."""
        stats = {
            "llm_calls": self._llm_calls,
            "tool_calls": self._tool_calls,
            "duration_ms": int((time.time() - self._start_time) * 1000),
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
        }
        if extra_stats:
            stats.update(extra_stats)

        self._write(SessionEndRecord(
            session_id=self.session_id,
            stats=stats,
        ).to_dict())
        self._file.flush()
        self._file.close()
        return stats

    def _write(self, record: dict) -> None:
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._file.closed:
            self.close()
