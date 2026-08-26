#!/usr/bin/env python3
"""Write user feedback into the project correction memory before a retry.

The next image authorization hashes this file and inlines every applicable
instruction into the canonical generation Prompt.  This prevents a review
finding from disappearing between conversational turns.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist user image-generation feedback as a hash-bound correction rule.")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--scope", required=True, choices=("shot", "project", "product", "style"))
    parser.add_argument("--target", required=True, help="Shot ID, project ID, product profile, style profile, or *.")
    parser.add_argument("--instruction", required=True, help="Positive, executable correction to include in the next Prompt.")
    parser.add_argument("--evidence", action="append", default=[], help="Observable evidence or source path; may be repeated.")
    parser.add_argument("--priority", type=int, default=100)
    parser.add_argument("--rule-id", help="Stable ID; defaults to a content-derived feedback ID.")
    args = parser.parse_args()
    project_dir = args.project_dir.expanduser().resolve()
    if not args.instruction.strip():
        raise ValueError("instruction must not be empty")
    if not 1 <= args.priority <= 100:
        raise ValueError("priority must be between 1 and 100")
    memory_path = project_dir / "library" / "correction_memory.json"
    memory = read_json(memory_path) if memory_path.is_file() else {"schema_version": "1.0", "version": 1, "rules": []}
    rules = memory.get("rules")
    if not isinstance(rules, list):
        raise ValueError("correction_memory.json.rules must be a list")
    fingerprint = hashlib.sha256(f"{args.scope}|{args.target}|{args.instruction}".encode("utf-8")).hexdigest()[:16]
    rule_id = args.rule_id or f"USER-FEEDBACK-{fingerprint}"
    rule = {
        "id": rule_id,
        "scope": args.scope,
        "target": args.target,
        "priority": args.priority,
        "instruction": args.instruction.strip(),
        "active": True,
        "origin": "user_feedback",
        "evidence": [item for item in args.evidence if item.strip()],
        "updated_at": now_iso(),
    }
    replaced = False
    for index, existing in enumerate(rules):
        if isinstance(existing, dict) and existing.get("id") == rule_id:
            rules[index] = rule
            replaced = True
            break
    if not replaced:
        rules.append(rule)
    memory["schema_version"] = memory.get("schema_version") or "1.0"
    memory["version"] = int(memory.get("version") or 0) + 1
    memory["rules"] = rules
    memory["updated_at"] = now_iso()
    write_json(memory_path, memory)
    memory_sha256 = hashlib.sha256(memory_path.read_bytes()).hexdigest()
    receipt = {
        "schema_version": "generation-feedback-writeback-v1.0",
        "status": "written",
        "rule_id": rule_id,
        "scope": args.scope,
        "target": args.target,
        "instruction": args.instruction.strip(),
        "evidence": rule["evidence"],
        "correction_memory": {"path": "library/correction_memory.json", "sha256": memory_sha256},
        "written_at": now_iso(),
    }
    receipt_path = project_dir / "review" / "feedback-writeback" / f"{rule_id}.json"
    write_json(receipt_path, receipt)
    print(json.dumps({**receipt, "receipt_path": str(receipt_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
