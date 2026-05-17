"""
Trajectory exporter: raw JSONL trace → SFT / tool-supervision / preference-pairs formats.
"""
import json
from pathlib import Path
from typing import Optional


def _load_trace(trace_file: Path) -> list[dict]:
    records = []
    with open(trace_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _get_session_meta(records: list[dict]) -> dict:
    for r in records:
        if r.get("type") == "session_start":
            return r
    return {}


def _get_session_stats(records: list[dict]) -> dict:
    for r in records:
        if r.get("type") == "session_end":
            return r.get("stats", {})
    return {}


def _flatten_content_to_text(content) -> str:
    """Convert content blocks to readable text for SFT format."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "thinking":
            # Include thinking in a collapsible section
            parts.append(f"<thinking>\n{block.get('thinking', '')}\n</thinking>")
        elif btype == "tool_use":
            tool_name = block.get("name", "")
            tool_input = json.dumps(block.get("input", {}), ensure_ascii=False, indent=2)
            parts.append(f"[Tool: {tool_name}]\n```json\n{tool_input}\n```")
        elif btype == "tool_result":
            inner = block.get("content", [])
            result_text = ""
            if isinstance(inner, list):
                result_text = "\n".join(
                    c.get("text", "") for c in inner if isinstance(c, dict)
                )
            elif isinstance(inner, str):
                result_text = inner
            parts.append(f"[Tool Result]\n{result_text}")
    return "\n\n".join(p for p in parts if p)


def _build_sft_messages_native(records: list[dict], system_prompt: Optional[str] = None) -> list[dict]:
    """
    Build SFT messages in OpenAI native tool_calls format.
    - assistant tool_use → {"role": "assistant", "tool_calls": [...]}
    - tool_result → {"role": "tool", "tool_call_id": ..., "content": ...}
    - assistant text → {"role": "assistant", "content": ...}
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    for record in records:
        rtype = record.get("type")
        if rtype not in ("user", "assistant"):
            continue

        msg = record.get("message", {})
        role = msg.get("role")
        content = msg.get("content", "")

        if role == "user":
            if isinstance(content, list) and all(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in content
            ):
                # Emit each tool_result as a separate "tool" role message
                for block in content:
                    tool_call_id = block.get("tool_use_id", "")
                    inner = block.get("content", [])
                    if isinstance(inner, list):
                        result_text = "\n".join(
                            c.get("text", "") for c in inner if isinstance(c, dict)
                        )
                    elif isinstance(inner, str):
                        result_text = inner
                    else:
                        result_text = str(inner)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result_text,
                    })
                continue

            # Real user message
            text = _flatten_content_to_text(content)
            if text.strip():
                messages.append({"role": "user", "content": text})

        elif role == "assistant":
            if not isinstance(content, list):
                text = str(content)
                if text.strip():
                    messages.append({"role": "assistant", "content": text})
                continue

            # Separate text/thinking blocks from tool_use blocks
            text_parts = []
            tool_calls = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "thinking":
                    text_parts.append(f"<thinking>\n{block.get('thinking', '')}\n</thinking>")
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    })

            if tool_calls:
                # Emit as native tool_calls message (with optional text content)
                assistant_msg: dict = {"role": "assistant", "tool_calls": tool_calls}
                combined_text = "\n\n".join(p for p in text_parts if p.strip())
                if combined_text.strip():
                    assistant_msg["content"] = combined_text
                messages.append(assistant_msg)
            elif text_parts:
                combined_text = "\n\n".join(p for p in text_parts if p.strip())
                if combined_text.strip():
                    messages.append({"role": "assistant", "content": combined_text})

    return messages


def export_sft(trace_file: Path, system_prompt: Optional[str] = None) -> Optional[dict]:
    """
    Convert a trace to OpenAI SFT format with native tool_calls structure.

    Returns a dict with 'messages' and 'metadata', or None if trace is invalid.
    """
    records = _load_trace(trace_file)
    meta = _get_session_meta(records)
    stats = _get_session_stats(records)

    if not meta:
        return None

    messages = _build_sft_messages_native(records, system_prompt=system_prompt)

    if len(messages) < 2:
        return None

    return {
        "messages": messages,
        "metadata": {
            "source": "code_review_agent",
            "pr": meta.get("pr", ""),
            "session_id": meta.get("session_id", ""),
            "decision": stats.get("decision", ""),
            "issues_found": stats.get("issues_found", 0),
            "llm_calls": stats.get("llm_calls", 0),
            "tool_calls": stats.get("tool_calls", 0),
        }
    }


def export_tool_supervision(trace_file: Path) -> list[dict]:
    """
    Tool supervision format: each (assistant_with_tool_use → tool_result) pair
    becomes a training sample for tool-calling ability.
    """
    records = _load_trace(trace_file)
    meta = _get_session_meta(records)
    samples = []

    # Build a map: tool_use_id → tool_result content
    tool_results: dict[str, str] = {}
    for record in records:
        if record.get("type") != "user":
            continue
        content = record.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tid = block.get("tool_use_id", "")
                inner = block.get("content", [])
                text = ""
                if isinstance(inner, list):
                    text = "\n".join(
                        c.get("text", "") for c in inner if isinstance(c, dict)
                    )
                tool_results[tid] = text

    # Extract tool_use blocks from assistant messages
    for record in records:
        if record.get("type") != "assistant":
            continue
        content = record.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tid = block.get("id", "")
            result = tool_results.get(tid, "")
            if result:
                samples.append({
                    "tool_name": block.get("name", ""),
                    "tool_input": block.get("input", {}),
                    "tool_result": result,
                    "metadata": {
                        "source": "code_review_agent",
                        "pr": meta.get("pr", ""),
                        "session_id": meta.get("session_id", ""),
                    }
                })

    return samples


def export_preference_pairs(trace_files: list[Path]) -> list[dict]:
    """
    DPO preference pairs: high-quality reviews (REQUEST_CHANGES with valid issues)
    vs low-quality reviews (COMMENT/APPROVE with few issues).

    Requires at least 2 trace files to compare.
    """
    candidates = []
    for tf in trace_files:
        stats = _get_session_stats(_load_trace(tf))
        meta = _get_session_meta(_load_trace(tf))
        sft = export_sft(tf)
        if sft:
            candidates.append({
                "sft": sft,
                "stats": stats,
                "pr": meta.get("pr", ""),
                "decision": stats.get("decision", ""),
                "issues_found": stats.get("issues_found", 0),
            })

    # Score: REQUEST_CHANGES + more issues = better
    def quality_score(c: dict) -> float:
        score = 0.0
        if c["decision"] == "REQUEST_CHANGES":
            score += 3.0
        elif c["decision"] == "COMMENT":
            score += 1.0
        score += min(c["issues_found"] * 0.5, 3.0)
        return score

    pairs = []
    sorted_candidates = sorted(candidates, key=quality_score, reverse=True)

    # Pair best with worst for same PR when possible, else across PRs
    for i in range(len(sorted_candidates) - 1):
        chosen = sorted_candidates[i]
        rejected = sorted_candidates[i + 1]
        if quality_score(chosen) > quality_score(rejected):
            pairs.append({
                "chosen": chosen["sft"]["messages"],
                "rejected": rejected["sft"]["messages"],
                "metadata": {
                    "source": "code_review_agent",
                    "chosen_pr": chosen["pr"],
                    "rejected_pr": rejected["pr"],
                    "chosen_decision": chosen["decision"],
                    "rejected_decision": rejected["decision"],
                }
            })

    return pairs


def export_directory(
    traces_dir: Path,
    output_dir: Path,
    system_prompt: Optional[str] = None,
    formats: Optional[list[str]] = None,
) -> dict:
    """
    Export all traces in a directory to the specified formats.

    formats: list of "sft", "tool-supervision", "preference-pairs"
    Returns a dict with counts per format.
    """
    if formats is None:
        formats = ["sft", "tool-supervision", "preference-pairs"]

    output_dir.mkdir(parents=True, exist_ok=True)
    trace_files = sorted(traces_dir.glob("trace_*.jsonl"))

    counts = {}

    if "sft" in formats:
        sft_path = output_dir / "sft_train.jsonl"
        count = 0
        with open(sft_path, "w", encoding="utf-8") as f:
            for tf in trace_files:
                sample = export_sft(tf, system_prompt=system_prompt)
                if sample:
                    f.write(json.dumps(sample, ensure_ascii=False) + "\n")
                    count += 1
        counts["sft"] = count
        print(f"SFT: {count} samples → {sft_path}")

    if "tool-supervision" in formats:
        ts_path = output_dir / "tool_supervision.jsonl"
        count = 0
        with open(ts_path, "w", encoding="utf-8") as f:
            for tf in trace_files:
                samples = export_tool_supervision(tf)
                for s in samples:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")
                    count += 1
        counts["tool-supervision"] = count
        print(f"Tool supervision: {count} samples → {ts_path}")

    if "preference-pairs" in formats:
        pp_path = output_dir / "preference_pairs.jsonl"
        pairs = export_preference_pairs(trace_files)
        with open(pp_path, "w", encoding="utf-8") as f:
            for p in pairs:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        counts["preference-pairs"] = len(pairs)
        print(f"Preference pairs: {len(pairs)} pairs → {pp_path}")

    return counts


# ─── Phase 3: orchestration trace export ──────────────────────────────────────

def export_orchestration_trace(
    session_id: str,
    trajectories_dir: Optional[Path] = None,
) -> dict:
    """Export a multi-agent orchestration trace as a single nested dict.

    Reads `orch_<session_id>.jsonl` (preferred) or `trace_<session_id>.jsonl`
    (fallback) from `trajectories_dir`, filters for orchestration_* events,
    and groups dispatched/completed events under each subtask. Suitable
    for direct rendering in a visualization layer.

    Returned shape:
        {
          "session_id": str,
          "source_file": str,
          "start":   {...} | None,
          "end":     {...} | None,
          "subtasks": [
              {"subtask_id": str, "role": str,
               "dispatched": {...}, "completed": {...}}
          ],
          "verdicts": [{...}],   # one per Reviewer round
          "raw_events": [...],   # all orchestration_* events in file order
          "error":   str         # only when source file is missing/empty
        }
    """
    trajectories_dir = trajectories_dir or Path("./trajectories")

    candidates = [
        trajectories_dir / f"orch_{session_id}.jsonl",
        trajectories_dir / f"trace_{session_id}.jsonl",
    ]
    source = next((p for p in candidates if p.exists()), None)
    if source is None:
        return {
            "session_id": session_id,
            "error": f"no trace found for session_id={session_id!r} "
                     f"in {trajectories_dir}",
            "subtasks": [], "verdicts": [], "raw_events": [],
            "start": None, "end": None,
        }

    records = _load_trace(source)

    # Filter to orchestration_* events; preserve file order.
    orch_types = {
        "orchestration_start", "subtask_dispatched",
        "worker_completed", "reviewer_verdict", "orchestration_end",
    }
    raw_events = [r for r in records if r.get("type") in orch_types]

    start_event: Optional[dict] = None
    end_event: Optional[dict] = None
    dispatched: dict[str, dict] = {}     # subtask_id → record
    completed: dict[str, dict] = {}
    verdicts: list[dict] = []

    for r in raw_events:
        rtype = r.get("type")
        if rtype == "orchestration_start" and start_event is None:
            start_event = r
        elif rtype == "orchestration_end":
            end_event = r
        elif rtype == "subtask_dispatched":
            dispatched[r.get("subtask_id", "")] = r
        elif rtype == "worker_completed":
            completed[r.get("subtask_id", "")] = r
        elif rtype == "reviewer_verdict":
            verdicts.append(r)

    # Join dispatched + completed by subtask_id; preserve dispatch order.
    subtasks: list[dict] = []
    seen: set[str] = set()
    for st_id, d in dispatched.items():
        seen.add(st_id)
        subtasks.append({
            "subtask_id": st_id,
            "role": d.get("role", ""),
            "dispatched": d,
            "completed": completed.get(st_id),
        })
    # Trail completed events that have no matching dispatch (defensive).
    for st_id, c in completed.items():
        if st_id not in seen:
            subtasks.append({
                "subtask_id": st_id,
                "role": c.get("role", ""),
                "dispatched": None,
                "completed": c,
            })

    return {
        "session_id": session_id,
        "source_file": str(source),
        "start": start_event,
        "end": end_event,
        "subtasks": subtasks,
        "verdicts": verdicts,
        "raw_events": raw_events,
    }
