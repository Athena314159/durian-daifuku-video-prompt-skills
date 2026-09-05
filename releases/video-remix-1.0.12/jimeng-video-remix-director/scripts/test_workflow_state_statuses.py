#!/usr/bin/env python3
"""Regression tests for source-ready, awaiting-input and blocked state separation."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent / "workflow_state.py"


def run(project: Path, command: list[str]) -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-dir", str(project), *command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="workflow-status-") as temporary:
        project = Path(temporary)
        (project / "project.json").write_text(
            json.dumps({"project_id": "P-INTAKE", "product_profile": None, "style_profile": "ugc-food-review-v1"}),
            encoding="utf-8",
        )

        initial = run(project, ["init"])
        assert initial["status"] == "in_progress"
        assert initial["pending_inputs"] == []

        source_ready = run(project, ["mark-source-ready"])
        assert source_ready["current_stage"] == "transcript_handoff"
        assert source_ready["source_ready"] is True
        assert source_ready["status"] == "source_ready"

        awaiting_script = run(project, ["await-input", "--input", "revised_script"])
        assert awaiting_script["status"] == "source_ready"
        assert awaiting_script["blocked_by"] == []

        awaiting_two = run(project, ["await-input", "--input", "target_product_reference"])
        assert awaiting_two["status"] == "source_ready"
        assert set(awaiting_two["pending_inputs"]) == {"revised_script", "target_product_reference"}

        blocked = run(project, ["block", "--code", "SOURCE_VIDEO_UNREADABLE", "--message", "ffprobe cannot read source"])
        assert blocked["status"] == "blocked"
        assert blocked["pending_inputs"]

        unblocked = run(project, ["resolve", "--code", "SOURCE_VIDEO_UNREADABLE"])
        assert unblocked["status"] == "source_ready"

        one_pending = run(project, ["resolve-input", "--input", "target_product_reference"])
        assert one_pending["status"] == "source_ready"
        finished_waiting = run(project, ["resolve-input", "--input", "revised_script"])
        assert finished_waiting["status"] == "source_ready"

    print("WORKFLOW STATE STATUS TESTS PASSED: 8 transitions")


if __name__ == "__main__":
    main()
