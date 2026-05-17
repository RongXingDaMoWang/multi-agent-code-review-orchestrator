"""
AgentRunner: Claude Code-style agent main loop.

Drives LLM + Tool calling until stop_reason == "end_turn".
Records complete trajectory (thinking + tool_use + tool_result).
"""
import json
import uuid
from pathlib import Path
from typing import Optional

from .llm_client import LLMClient
from .tool_router import ToolRouter
from .skill_loader import SkillLoader
from ..trajectory.logger import TrajectoryLogger
from ..trajectory.schemas import AgentResult, ToolUseBlock


class AgentRunner:
    """
    Main agent loop, analogous to Claude Code's execution model.

    Flow per iteration:
    1. Call LLM with system prompt + message history + tool definitions
    2. Log assistant response (thinking + text + tool_use blocks)
    3. If stop_reason == "end_turn": break
    4. Execute each tool_use block via ToolRouter
    5. Log each tool_result
    6. Append assistant content + tool_results to messages
    7. Repeat
    """

    MAX_ITERATIONS = 30  # Safety limit to prevent infinite loops

    def __init__(
        self,
        llm_client: LLMClient,
        tool_router: ToolRouter,
        skill_loader: SkillLoader,
        trajectories_dir: Path,
        skill_name: str = "code-review",
    ):
        self.llm_client = llm_client
        self.tool_router = tool_router
        self.skill_loader = skill_loader
        self.trajectories_dir = trajectories_dir
        self.skill_name = skill_name

        # Load system prompt from all sub-skills (main + triage + analyze + act + memory)
        try:
            self.system_prompt = self.skill_loader.load_combined(
                skill_name,
                f"{skill_name}-triage",
                f"{skill_name}-analyze",
                f"{skill_name}-act",
                f"{skill_name}-memory",
            )
        except Exception:
            self.system_prompt = (
                "You are a professional code reviewer. "
                "Analyze pull requests for security issues, bugs, and code quality."
            )

    def run(self, task: str, pr: str = "", extra_context: Optional[dict] = None) -> AgentResult:
        """
        Execute the agent loop for a given task.

        Args:
            task: Natural language task description (e.g. "review owner/repo #42")
            pr: PR identifier for trajectory naming (e.g. "owner/repo#42")
            extra_context: Optional extra data to inject into the system prompt
        """
        session_id = uuid.uuid4().hex[:12]
        pr_label = pr or task[:40]

        # Build system prompt (optionally inject extra context)
        system = self.system_prompt
        if extra_context:
            context_json = json.dumps(extra_context, ensure_ascii=False, indent=2)
            system = f"{system}\n\n---\n\n## Context\n```json\n{context_json}\n```"

        messages: list[dict] = [{"role": "user", "content": task}]

        with TrajectoryLogger(
            output_dir=self.trajectories_dir,
            session_id=session_id,
            pr=pr_label,
        ) as logger:
            logger.log_user_input(task)

            iteration = 0
            final_text = ""
            review_decision = ""
            issues_found = 0

            while iteration < self.MAX_ITERATIONS:
                iteration += 1

                # 1. Call LLM
                response = self.llm_client.complete(
                    messages=messages,
                    system=system,
                    tools=self.tool_router.tool_definitions,
                )

                # 2. Log assistant response
                logger.log_assistant(response)

                # 3. Check stop condition
                if response.stop_reason == "end_turn":
                    final_text = response.text
                    messages.append({"role": "assistant", "content": response.content})
                    break

                # 4. Execute tool calls
                tool_results_content = []
                for tool_use_block in response.tool_uses:
                    result = self.tool_router.execute(
                        tool_use_block.name,
                        tool_use_block.input,
                    )

                    # 5. Log tool result
                    logger.log_tool_result(tool_use_block, result)

                    # Build tool_result content block for next message
                    result_text = (
                        json.dumps(result, ensure_ascii=False)
                        if isinstance(result, (dict, list))
                        else str(result)
                    )
                    tool_results_content.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_block.id,
                        "content": [{"type": "text", "text": result_text}],
                    })

                # 6. Append to message history
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results_content})

            else:
                final_text = f"[Agent stopped after {self.MAX_ITERATIONS} iterations]"

            # Close trajectory with stats
            # Extract decision and issues_found from all assistant messages
            all_text = " ".join(
                b.get("text", "")
                for m in messages if m.get("role") == "assistant"
                for b in (m.get("content") if isinstance(m.get("content"), list) else [])
                if isinstance(b, dict) and b.get("type") == "text"
            )
            if "REQUEST_CHANGES" in all_text or "REQUEST CHANGES" in all_text:
                review_decision = "REQUEST_CHANGES"
            elif "APPROVE" in all_text:
                review_decision = "APPROVE"
            elif "COMMENT" in all_text:
                review_decision = "COMMENT"

            # Extract issues_found from JSON blocks in assistant messages
            import re as _re
            for json_match in _re.finditer(r'\{[^{}]*"issues"\s*:\s*\[', all_text):
                # Try to parse the JSON object starting at this position
                start = json_match.start()
                # Find matching closing brace by counting depth
                depth = 0
                end = start
                for i, ch in enumerate(all_text[start:], start):
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                try:
                    parsed = json.loads(all_text[start:end])
                    if isinstance(parsed.get("issues"), list):
                        issues_found = max(issues_found, len(parsed["issues"]))
                except (json.JSONDecodeError, ValueError):
                    pass

            extra_stats = {
                "decision": review_decision,
                "issues_found": issues_found,
                "iterations": iteration,
            }
            stats = logger.close(extra_stats=extra_stats)

        return AgentResult(
            messages=messages,
            final_response=final_text,
            session_id=session_id,
            stats=stats,
        )
