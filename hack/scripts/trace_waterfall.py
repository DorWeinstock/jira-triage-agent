#!/usr/bin/env python3
"""Display LangSmith trace waterfall with timing like the UI."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml


def load_langsmith_config():
    """Load API key from langsmith-fetch config."""
    config_path = Path.home() / ".langsmith-cli" / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f)
            if config and "api_key" in config:
                os.environ["LANGSMITH_API_KEY"] = config["api_key"]
                return True
    return False


# Load config before importing Client
load_langsmith_config()

from langsmith import Client


def format_duration(seconds: float) -> str:
    """Format duration in human-readable form."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    elif seconds < 60:
        return f"{seconds:.2f}s"
    else:
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins}m {secs:.1f}s"


def get_model_name(run) -> str:
    """Extract model name from run metadata."""
    if not run.extra:
        return ""

    # Try invocation_params first
    invocation = run.extra.get('invocation_params', {})
    model = invocation.get('model') or invocation.get('model_name')
    if model:
        return model

    # Try metadata
    metadata = run.extra.get('metadata', {})
    return metadata.get('ls_model_name', '')


def print_trace_waterfall(trace_id: str):
    """Print trace with all child steps in waterfall format."""
    client = Client()

    # Fetch all runs in this trace
    runs = list(client.list_runs(trace_id=trace_id))

    if not runs:
        print(f"No runs found for trace {trace_id}")
        return

    # Build parent-child relationships
    runs_by_id = {str(run.id): run for run in runs}
    children = {}
    root_runs = []

    for run in runs:
        parent_id = str(run.parent_run_id) if run.parent_run_id else None
        if parent_id and parent_id in runs_by_id:
            children.setdefault(parent_id, []).append(run)
        else:
            root_runs.append(run)

    # Sort children by start time
    for parent_id in children:
        children[parent_id].sort(key=lambda r: r.start_time or datetime.min)

    root_runs.sort(key=lambda r: r.start_time or datetime.min)

    # Find trace start time for relative timing
    trace_start = min(r.start_time for r in runs if r.start_time)

    def get_total_tokens() -> tuple[int, int]:
        """Get total tokens across all LLM runs (no double counting)."""
        prompt = 0
        completion = 0
        for run in runs:
            if run.run_type == "llm":
                prompt += run.prompt_tokens or 0
                completion += run.completion_tokens or 0
        return prompt, completion

    def get_descendant_models(run) -> set[str]:
        """Get all unique model names used by descendants."""
        models = set()

        if run.run_type == "llm":
            model = get_model_name(run)
            if model:
                models.add(model)

        run_id = str(run.id)
        if run_id in children:
            for child in children[run_id]:
                models.update(get_descendant_models(child))

        return models

    def print_run(run, indent: int = 0):
        """Print a single run with its children."""
        prefix = "│   " * indent

        # Status indicator
        if run.status == "success":
            status = "\033[32m✓\033[0m"  # Green checkmark
        elif run.status == "error":
            status = "\033[31m✗\033[0m"  # Red X
        else:
            status = "\033[33m●\033[0m"  # Yellow dot (pending/running)

        # Timing (latency can be float or timedelta)
        if run.latency:
            if hasattr(run.latency, 'total_seconds'):
                duration = format_duration(run.latency.total_seconds())
            else:
                duration = format_duration(float(run.latency))
        else:
            duration = "?"

        # Relative start time
        if run.start_time:
            rel_start = (run.start_time - trace_start).total_seconds()
            start_str = f"@{format_duration(rel_start)}"
        else:
            start_str = ""

        # Run type styling
        run_type = run.run_type
        if run_type == "llm":
            type_str = "\033[35mllm\033[0m"  # Magenta
        elif run_type == "tool":
            type_str = "\033[36mtool\033[0m"  # Cyan
        elif run_type == "chain":
            type_str = "\033[34mchain\033[0m"  # Blue
        else:
            type_str = run_type

        # Token info (only show for LLM runs to avoid double-counting display)
        if run.run_type == "llm":
            prompt_tok = run.prompt_tokens or 0
            completion_tok = run.completion_tokens or 0
            if prompt_tok or completion_tok:
                tokens_str = f" \033[33m[{prompt_tok}→{completion_tok} tok]\033[0m"
            else:
                tokens_str = ""
        else:
            tokens_str = ""

        # Model info
        if run.run_type == "llm":
            model = get_model_name(run)
            if model:
                model_str = f" \033[36m({model})\033[0m"
            else:
                model_str = ""
        else:
            # For chains, show models used by descendants
            models = get_descendant_models(run)
            if models:
                model_str = f" \033[36m({', '.join(sorted(models))})\033[0m"
            else:
                model_str = ""

        # Print the run
        print(f"{prefix}{status} {run.name} ({type_str}) {start_str} → {duration}{tokens_str}{model_str}")

        # Print error if present
        if run.error:
            error_prefix = "│   " * (indent + 1)
            error_msg = run.error[:100] + "..." if len(run.error) > 100 else run.error
            print(f"{error_prefix}\033[31m└─ Error: {error_msg}\033[0m")

        # Print children
        run_id = str(run.id)
        if run_id in children:
            for child in children[run_id]:
                print_run(child, indent + 1)

    # Print header
    root = root_runs[0] if root_runs else runs[0]
    if root.latency:
        if hasattr(root.latency, 'total_seconds'):
            total_duration = format_duration(root.latency.total_seconds())
        else:
            total_duration = format_duration(float(root.latency))
    else:
        total_duration = "?"

    # Total tokens across trace (sum of all LLM calls, no double counting)
    total_prompt, total_completion = get_total_tokens()

    print(f"\n\033[1mTrace: {trace_id}\033[0m")
    print(f"Total: {total_duration} | Steps: {len(runs)} | Tokens: {total_prompt}→{total_completion}")
    print("─" * 70)

    # Print all root runs
    for run in root_runs:
        print_run(run)

    print()


def get_trace_as_json(trace_id: str) -> dict:
    """Get trace data as structured JSON for LLM consumption."""
    client = Client()

    runs = list(client.list_runs(trace_id=trace_id))

    if not runs:
        return {"error": f"No runs found for trace {trace_id}"}

    # Build parent-child relationships
    runs_by_id = {str(run.id): run for run in runs}
    children = {}
    root_runs = []

    for run in runs:
        parent_id = str(run.parent_run_id) if run.parent_run_id else None
        if parent_id and parent_id in runs_by_id:
            children.setdefault(parent_id, []).append(run)
        else:
            root_runs.append(run)

    # Sort children by start time
    for parent_id in children:
        children[parent_id].sort(key=lambda r: r.start_time or datetime.min)

    root_runs.sort(key=lambda r: r.start_time or datetime.min)

    # Find trace start time for relative timing
    trace_start = min(r.start_time for r in runs if r.start_time)

    def get_total_tokens() -> tuple[int, int]:
        prompt = 0
        completion = 0
        for run in runs:
            if run.run_type == "llm":
                prompt += run.prompt_tokens or 0
                completion += run.completion_tokens or 0
        return prompt, completion

    def get_latency_seconds(run) -> float | None:
        if not run.latency:
            return None
        if hasattr(run.latency, 'total_seconds'):
            return round(run.latency.total_seconds(), 3)
        return round(float(run.latency), 3)

    def build_run_dict(run) -> dict:
        """Build a dictionary representation of a run."""
        rel_start = None
        if run.start_time:
            rel_start = round((run.start_time - trace_start).total_seconds(), 3)

        run_dict = {
            "name": run.name,
            "type": run.run_type,
            "status": run.status,
            "start_offset_seconds": rel_start,
            "duration_seconds": get_latency_seconds(run),
        }

        # Add token info for LLM runs
        if run.run_type == "llm":
            run_dict["tokens"] = {
                "prompt": run.prompt_tokens or 0,
                "completion": run.completion_tokens or 0,
                "total": (run.prompt_tokens or 0) + (run.completion_tokens or 0),
            }
            model = get_model_name(run)
            if model:
                run_dict["model"] = model

        # Add error if present
        if run.error:
            run_dict["error"] = run.error

        # Add children
        run_id = str(run.id)
        if run_id in children:
            run_dict["children"] = [
                build_run_dict(child) for child in children[run_id]
            ]

        return run_dict

    # Build the response
    root = root_runs[0] if root_runs else runs[0]
    total_prompt, total_completion = get_total_tokens()

    # Count LLM calls
    llm_count = sum(1 for r in runs if r.run_type == "llm")

    result = {
        "trace_id": trace_id,
        "summary": {
            "total_duration_seconds": get_latency_seconds(root),
            "total_steps": len(runs),
            "llm_calls": llm_count,
            "tokens": {
                "prompt": total_prompt,
                "completion": total_completion,
                "total": total_prompt + total_completion,
            },
            "status": root.status,
        },
        "analysis_hints": {
            "slow_threshold_seconds": 10,
            "high_token_threshold": 2000,
            "look_for": [
                "Steps with long duration but no LLM calls (waiting/polling)",
                "LLM calls with high token counts (context bloat)",
                "Failed steps and their error messages",
                "Sequential vs parallel execution patterns",
            ],
        },
        "steps": [build_run_dict(run) for run in root_runs],
    }

    return result


def print_trace_json(trace_id: str):
    """Print trace as JSON for LLM consumption."""
    result = get_trace_as_json(trace_id)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python trace_waterfall.py <trace-id> [--json]")
        sys.exit(1)

    trace_id = sys.argv[1]
    use_json = "--json" in sys.argv

    if use_json:
        print_trace_json(trace_id)
    else:
        print_trace_waterfall(trace_id)
