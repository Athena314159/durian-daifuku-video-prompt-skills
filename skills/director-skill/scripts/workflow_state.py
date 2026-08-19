#!/usr/bin/env python3
"""Initialize and update per-project workflow state without shared global state."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


STAGES = [
    "intake",
    "transcript_handoff",
    "revised_script_lock",
    "role_lock",
    "asset_inventory",
    "storyboard_approval",
    "first_frame_approval",
    "prompt_compile",
    "text_image_alignment",
    "docx_render_qa",
    "complete",
]


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


@contextmanager
def project_lock(project_dir: Path) -> Iterator[None]:
    lock_path = project_dir / "planning" / "workflow_state.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def initialize(project_dir: Path, skill_dir: Path) -> dict[str, Any]:
    project = read_json(project_dir / "project.json")
    path = project_dir / "planning" / "workflow_state.json"
    state = read_json(path) if path.is_file() else {}
    state.update(
        {
            "schema_version": "1.0",
            "project_id": project.get("project_id"),
            "current_stage": state.get("current_stage") or "intake",
            "status": state.get("status") or "in_progress",
            "canonical_prompt_source": "prompts",
            "completed_stages": state.get("completed_stages") or [],
            "blocked_by": state.get("blocked_by") or [],
            "next_allowed_actions": state.get("next_allowed_actions")
            or ["import_source_video", "record_subtitle_script", "bind_product_profile"],
            "skill_versions": {
                "director-skill": {"sha256": digest(skill_dir / "SKILL.md")},
                "product_profile": project.get("product_profile"),
                "style_profile": project.get("style_profile"),
            },
            "updated_at": now_iso(),
        }
    )
    write_json(path, state)
    candidates = project_dir / "planning" / "skill_update_candidates.json"
    candidate_data = read_json(candidates) if candidates.is_file() else {"schema_version": "1.0", "candidates": []}
    candidate_data["project_id"] = project.get("project_id")
    write_json(candidates, candidate_data)
    return state


def set_stage(state: dict[str, Any], stage: str, actions: list[str]) -> None:
    previous = state.get("current_stage")
    if previous in STAGES and STAGES.index(previous) < STAGES.index(stage):
        completed = state.setdefault("completed_stages", [])
        for item in STAGES[STAGES.index(previous) : STAGES.index(stage)]:
            if item not in completed:
                completed.append(item)
    state["current_stage"] = stage
    state["next_allowed_actions"] = actions
    state["status"] = "complete" if stage == "complete" else ("blocked" if state.get("blocked_by") else "in_progress")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage one project's resumable workflow state.")
    parser.add_argument("--project-dir", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    stage_parser = sub.add_parser("set-stage")
    stage_parser.add_argument("--stage", required=True, choices=STAGES)
    stage_parser.add_argument("--next-action", action="append", default=[])
    block_parser = sub.add_parser("block")
    block_parser.add_argument("--code", required=True)
    block_parser.add_argument("--message", required=True)
    block_parser.add_argument("--shot-id")
    resolve_parser = sub.add_parser("resolve")
    resolve_parser.add_argument("--code", required=True)
    args = parser.parse_args()

    project_dir = args.project_dir.expanduser().resolve()
    state_path = project_dir / "planning" / "workflow_state.json"
    skill_dir = Path(__file__).resolve().parent.parent
    with project_lock(project_dir):
        state = initialize(project_dir, skill_dir)
        if args.command == "set-stage":
            set_stage(state, args.stage, args.next_action)
        elif args.command == "block":
            blockers = [item for item in state.get("blocked_by", []) if item.get("code") != args.code]
            blockers.append({"code": args.code, "message": args.message, "shot_id": args.shot_id})
            state["blocked_by"] = blockers
            state["status"] = "blocked"
        elif args.command == "resolve":
            state["blocked_by"] = [item for item in state.get("blocked_by", []) if item.get("code") != args.code]
            state["status"] = "blocked" if state["blocked_by"] else "in_progress"
        state["updated_at"] = now_iso()
        write_json(state_path, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
