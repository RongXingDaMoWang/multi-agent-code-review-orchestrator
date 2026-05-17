#!/usr/bin/env python3
"""
Code Review Agent CLI.

Usage:
  python cli.py review owner/repo 42
  python cli.py review https://github.com/owner/repo/pull/42
  python cli.py batch-review --prs prs.txt
  python cli.py export --input ./trajectories --format sft
  python cli.py inspect traces/trace_abc123.jsonl
"""
import argparse
import json
import os
import sys
from pathlib import Path

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from code_review_agent.config import Config
from code_review_agent.pipeline.orchestrator import ReviewOrchestrator, parse_pr_identifier
from code_review_agent.trajectory.exporter import export_directory
from code_review_agent.trajectory.schemas import LLMResponse


def cmd_review(args) -> int:
    """Review a single PR."""
    # Build PR identifier from positional args or --pr flag
    if args.pr:
        pr_input = args.pr
    elif len(args.pr_parts) >= 2:
        # "owner/repo 42" → "owner/repo #42"
        pr_input = " #".join(args.pr_parts[:2])
    elif len(args.pr_parts) == 1:
        pr_input = args.pr_parts[0]
    else:
        print("Error: provide PR as 'owner/repo 42' or --pr owner/repo#42")
        return 1

    orchestrator = ReviewOrchestrator(
        github_token=args.github_token or Config.GITHUB_TOKEN,
        anthropic_api_key=args.api_key or Config.ANTHROPIC_API_KEY,
        model=args.model or Config.ANTHROPIC_MODEL,
        base_url=Config.OPENAI_BASE_URL,
        trajectories_dir=Path(args.trajectories_dir),
        dry_run=args.dry_run,
        enable_thinking=not args.no_thinking,
    )

    print(f"Reviewing PR: {pr_input}")
    if getattr(args, "mode", "single") == "multi":
        print("Mode: multi-agent (Leader → Workers → Reviewer)")
        result = orchestrator.multi_agent_review(pr_input)
    else:
        result = orchestrator.single_agent_review(pr_input)

    if result.error:
        print(f"Error: {result.error}")
        return 1

    print(f"\n## Code Review Complete")
    print(f"PR:       {result.pr}")
    print(f"Decision: {result.decision or '(pending)'}")
    print(f"Issues:   {result.issues_found}")
    print(f"Session:  {result.session_id}")
    print(f"Trace:    {args.trajectories_dir}/trace_{result.session_id}.jsonl")

    if result.multi_agent:
        ma = result.multi_agent
        print(f"\n## Multi-Agent Summary")
        print(ma.get("summary", ""))
        per_role = ma.get("per_role", {})
        if per_role:
            print("\nPer-role:")
            for role, info in per_role.items():
                print(f"  - {role}: status={info.get('status')} "
                      f"conf={info.get('confidence')} "
                      f"findings={info.get('finding_count')} "
                      f"tokens={info.get('tokens')}")
        if ma.get("conflicts"):
            print(f"\nConflicts detected: {len(ma['conflicts'])}")
        recs = ma.get("recommendations", [])
        if recs:
            print("\nRecommendations:")
            for r in recs:
                print(f"  - {r}")
    return 0


def cmd_batch_review(args) -> int:
    """Review multiple PRs from a file."""
    prs_file = Path(args.prs)
    if not prs_file.exists():
        print(f"Error: {prs_file} not found")
        return 1

    pr_list = [line.strip() for line in prs_file.read_text().splitlines()
               if line.strip() and not line.startswith("#")]

    print(f"Batch reviewing {len(pr_list)} PRs...")

    orchestrator = ReviewOrchestrator(
        github_token=args.github_token or Config.GITHUB_TOKEN,
        anthropic_api_key=args.api_key or Config.ANTHROPIC_API_KEY,
        model=args.model or Config.ANTHROPIC_MODEL,
        base_url=Config.OPENAI_BASE_URL,
        trajectories_dir=Path(args.trajectories_dir),
        dry_run=args.dry_run,
        enable_thinking=not args.no_thinking,
    )

    results = orchestrator.batch_review(pr_list, max_workers=args.workers)

    ok = sum(1 for r in results if not r.error)
    print(f"\nCompleted: {ok}/{len(results)} successful")
    for r in results:
        status = "✓" if not r.error else "✗"
        print(f"  {status} {r.pr}: {r.decision or r.error or '?'}")

    return 0 if ok == len(results) else 1


def cmd_export(args) -> int:
    """Export trajectories to training data formats."""
    input_dir = Path(args.input)
    output_dir = Path(args.output)

    if not input_dir.exists():
        print(f"Error: {input_dir} not found")
        return 1

    formats = args.format if args.format else ["sft", "tool-supervision", "preference-pairs"]

    # Load system prompt for SFT export
    system_prompt = None
    if "sft" in formats:
        try:
            from code_review_agent.agent.skill_loader import SkillLoader
            system_prompt = SkillLoader().load_skill("code-review")
        except Exception:
            pass

    print(f"Exporting from {input_dir} → {output_dir}")
    counts = export_directory(
        traces_dir=input_dir,
        output_dir=output_dir,
        system_prompt=system_prompt,
        formats=formats,
    )

    print(f"\nExport complete:")
    for fmt, count in counts.items():
        print(f"  {fmt}: {count} samples")
    return 0


def cmd_health_report(args) -> int:
    """Generate a health report for a repository based on accumulated review data."""
    repo = args.repo
    output_path = Path(args.output) if args.output else None

    orchestrator = ReviewOrchestrator(
        github_token=args.github_token or Config.GITHUB_TOKEN,
        anthropic_api_key=args.api_key or Config.ANTHROPIC_API_KEY,
        model=args.model or Config.ANTHROPIC_MODEL,
        base_url=Config.OPENAI_BASE_URL,
        trajectories_dir=Path(args.trajectories_dir),
        dry_run=False,
        enable_thinking=False,
    )

    print(f"Generating health report for: {repo}")
    report = orchestrator.generate_health_report(repo)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"Report saved to: {output_path}")
    else:
        print("\n" + report)

    return 0


def cmd_inspect(args) -> int:
    """Inspect a trajectory JSONL file."""
    trace_file = Path(args.trace_file)
    if not trace_file.exists():
        print(f"Error: {trace_file} not found")
        return 1

    records = []
    with open(trace_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    print(f"Trace: {trace_file}")
    print(f"Records: {len(records)}\n")

    for i, record in enumerate(records):
        rtype = record.get("type", "?")
        if rtype == "session_start":
            print(f"[{i}] SESSION_START  pr={record.get('pr')}  id={record.get('session_id')}")
        elif rtype == "session_end":
            stats = record.get("stats", {})
            print(f"[{i}] SESSION_END    llm_calls={stats.get('llm_calls')}  "
                  f"tool_calls={stats.get('tool_calls')}  "
                  f"duration={stats.get('duration_ms')}ms  "
                  f"decision={stats.get('decision')}")
        elif rtype == "user":
            content = record.get("message", {}).get("content", "")
            if isinstance(content, str):
                preview = content[:80].replace("\n", " ")
                print(f"[{i}] USER          {preview!r}")
            elif isinstance(content, list):
                # Tool results
                tool_ids = [b.get("tool_use_id", "?") for b in content
                            if isinstance(b, dict) and b.get("type") == "tool_result"]
                if tool_ids:
                    print(f"[{i}] TOOL_RESULT   ids={tool_ids}")
                else:
                    print(f"[{i}] USER          (list content, {len(content)} blocks)")
        elif rtype == "assistant":
            msg = record.get("message", {})
            content = msg.get("content", [])
            stop = msg.get("stop_reason", "?")
            usage = msg.get("usage", {})
            blocks = []
            for b in content:
                if isinstance(b, dict):
                    bt = b.get("type", "?")
                    if bt == "thinking":
                        blocks.append(f"thinking({len(b.get('thinking',''))}chars)")
                    elif bt == "text":
                        blocks.append(f"text({len(b.get('text',''))}chars)")
                    elif bt == "tool_use":
                        blocks.append(f"tool_use({b.get('name')})")
            print(f"[{i}] ASSISTANT     stop={stop}  "
                  f"in={usage.get('input_tokens')}  out={usage.get('output_tokens')}  "
                  f"blocks=[{', '.join(blocks)}]")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-review-agent",
        description="Code Review Agent with trajectory collection for fine-tuning",
    )

    # Common options
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--github-token", default="", help="GitHub token (or set GITHUB_TOKEN)")
    common.add_argument("--api-key", default="", help="Anthropic API key (or set ANTHROPIC_API_KEY)")
    common.add_argument("--model", default="", help="Model name (default: claude-opus-4-6)")
    common.add_argument("--trajectories-dir", default="./trajectories", help="Trajectories output dir")
    common.add_argument("--dry-run", action="store_true", help="Skip write operations (GitHub)")
    common.add_argument("--no-thinking", action="store_true", help="Disable Extended Thinking")

    subparsers = parser.add_subparsers(dest="command")

    # review
    p_review = subparsers.add_parser("review", parents=[common], help="Review a single PR")
    p_review.add_argument("pr_parts", nargs="*", help="PR as 'owner/repo 42' or URL")
    p_review.add_argument("--pr", default="", help="PR identifier (alternative)")
    p_review.add_argument(
        "--mode",
        choices=["single", "multi"],
        default="single",
        help="Review mode: 'single' (default, backward-compatible) or 'multi' "
             "(Leader-Worker-Reviewer orchestration).",
    )
    p_review.set_defaults(func=cmd_review)

    # batch-review
    p_batch = subparsers.add_parser("batch-review", parents=[common], help="Review multiple PRs")
    p_batch.add_argument("--prs", required=True, help="File with PR identifiers (one per line)")
    p_batch.add_argument("--workers", type=int, default=1, help="Concurrent workers")
    p_batch.set_defaults(func=cmd_batch_review)

    # export
    p_export = subparsers.add_parser("export", help="Export trajectories to training data")
    p_export.add_argument("--input", required=True, help="Trajectories directory")
    p_export.add_argument("--output", default="./exports", help="Output directory")
    p_export.add_argument(
        "--format", nargs="+",
        choices=["sft", "tool-supervision", "preference-pairs"],
        help="Export formats (default: all)",
    )
    p_export.set_defaults(func=cmd_export)

    # health-report
    p_health = subparsers.add_parser("health-report", parents=[common],
                                     help="Generate a health report for a repository")
    p_health.add_argument("--repo", required=True,
                          help="Repository in owner/repo format")
    p_health.add_argument("--output", default="",
                          help="Output file path (default: print to stdout)")
    p_health.set_defaults(func=cmd_health_report)

    # inspect
    p_inspect = subparsers.add_parser("inspect", help="Inspect a trajectory file")
    p_inspect.add_argument("trace_file", help="Path to trace_*.jsonl file")
    p_inspect.set_defaults(func=cmd_inspect)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
