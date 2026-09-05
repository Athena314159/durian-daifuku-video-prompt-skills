#!/usr/bin/env python3
"""Propagate an active image revocation to every downstream delivery state."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_object(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def invalidate_delivery(project_dir: Path, revocation_path: Path) -> dict[str, Any]:
    project_dir = project_dir.expanduser().resolve()
    revocation_path = revocation_path.expanduser().resolve()
    revocation = read_object(revocation_path)
    if revocation.get("status") != "active":
        raise ValueError("only status=active revocations can invalidate delivery")
    revoked_asset_ids = revocation.get("revoked_asset_ids")
    if not isinstance(revoked_asset_ids, list) or not revoked_asset_ids:
        revoked_asset_ids = [
            item.get("asset_id")
            for item in (revocation.get("revoked_assets") or [])
            if isinstance(item, dict) and isinstance(item.get("asset_id"), str) and item["asset_id"].strip()
        ]
    reason_codes = revocation.get("reason_codes") or revocation.get("reject_codes") or revocation.get("observed_failures")
    if not isinstance(revoked_asset_ids, list) or not revoked_asset_ids:
        raise ValueError("revocation requires revoked_asset_ids")
    if not isinstance(reason_codes, list) or not reason_codes:
        raise ValueError("revocation requires reason_codes")

    invalidated_at = datetime.now().astimezone().replace(microsecond=0).isoformat()
    workflow_path = project_dir / "planning" / "workflow_state.json"
    workflow = read_object(workflow_path)
    workflow["status"] = "images_revoked"
    workflow["docx_export_authorized"] = False
    workflow["revoked_asset_ids"] = revoked_asset_ids
    workflow["revocation_reason_codes"] = reason_codes
    workflow["active_revocation_path"] = str(revocation_path)
    workflow["updated_at"] = invalidated_at
    actions = workflow.get("next_allowed_actions")
    workflow["next_allowed_actions"] = [
        item for item in actions if item not in {"compile", "export_docx", "align_exports", "deliver"}
    ] if isinstance(actions, list) else []
    if "regenerate_revoked_assets" not in workflow["next_allowed_actions"]:
        workflow["next_allowed_actions"].append("regenerate_revoked_assets")
    write_object(workflow_path, workflow)

    for relative in (Path("prompts/generation_pack.json"), Path("exports/export_manifest.json")):
        path = project_dir / relative
        if not path.is_file():
            continue
        value = read_object(path)
        value["status"] = "stale_due_to_revocation"
        value["docx_export_authorized"] = False
        value["revoked_asset_ids"] = revoked_asset_ids
        value["revocation_reason_codes"] = reason_codes
        value["invalidated_at"] = invalidated_at
        value["active_revocation_path"] = str(revocation_path)
        write_object(path, value)

    receipt = {
        "schema_version": "delivery-revocation-cascade-v1.0",
        "status": "invalidated",
        "invalidated_at": invalidated_at,
        "revocation_path": str(revocation_path),
        "revoked_asset_ids": revoked_asset_ids,
        "reason_codes": reason_codes,
        "docx_export_authorized": False,
        "recovery_requirement": "replace every revoked asset, show the complete updated gallery, record a new user approval receipt, recompile, then re-export Word",
    }
    write_object(project_dir / "review" / "delivery_revocation_cascade.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--revocation", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = invalidate_delivery(args.project_dir, args.revocation)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"REVOCATION_CASCADE_BLOCKED: {exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
