#!/usr/bin/env python3
"""Validate the full-delivery text handoff with the director's v2 contract.

This entry point deliberately delegates all semantic-lock validation to the
canonical ``jimeng-video-remix-director`` validator.  The text skill therefore
cannot drift into a second interpretation of S/SRC/ADD ownership, timing,
six-layer evidence, packaging, eating events, or break events.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any


SCHEMA_VERSION = "text-handoff-v2.0"


@dataclass(frozen=True)
class Issue:
    """Stable extract-skill wrapper around one director validation error."""

    code: str
    message: str


def _director_validator_path() -> Path:
    skill_root = Path(__file__).resolve().parents[1]
    return skill_root.parent / "jimeng-video-remix-director" / "scripts" / "validate_branch_handoff.py"


def _load_director_validator() -> ModuleType:
    path = _director_validator_path()
    if not path.is_file():
        raise FileNotFoundError(
            "缺少 jimeng-video-remix-director 的 canonical validator："
            f"{path}"
        )
    spec = importlib.util.spec_from_file_location("_jimeng_validate_branch_handoff", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 canonical validator：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compute_shot_map_sha256(data: dict[str, Any]) -> str:
    """Return the same six-field semantic digest used by both v2 branches."""

    module = _load_director_validator()
    return str(module.semantic_shot_map_sha256(data))


def _issue_code(message: str) -> str:
    if "schema_version" in message or "v1 full-delivery" in message:
        return "TEXT_HANDOFF_VERSION_INVALID"
    if "locked_semantic_hash" in message or "shot_map_sha256" in message:
        return "TEXT_HANDOFF_HASH_INVALID"
    if "collections" in message or "complete locked" in message:
        return "TEXT_HANDOFF_COLLECTION_INVALID"
    if "six-layer" in message or "source_performance_layers" in message:
        return "TEXT_HANDOFF_SIX_LAYER_INVALID"
    if "packaging" in message or "visible_faces" in message:
        return "TEXT_HANDOFF_PACKAGING_INVALID"
    if "eating" in message:
        return "TEXT_HANDOFF_EATING_PLAN_INVALID"
    if "break" in message or "crisp" in message or "crumbs" in message:
        return "TEXT_HANDOFF_BREAK_PLAN_INVALID"
    if "timecode" in message or "duration" in message or "timeline" in message:
        return "TEXT_HANDOFF_TIMECODE_INVALID"
    if "missing" in message:
        return "TEXT_HANDOFF_FIELD_MISSING"
    return "TEXT_HANDOFF_V2_INVALID"


def validate_text_handoff(
    data: dict[str, Any],
    *,
    locked_shot_map: dict[str, Any] | None = None,
) -> list[Issue]:
    """Validate one text-handoff-v2.0 against the immutable semantic lock.

    The public merge CLI always requires an external lock.  The optional API
    fallback exists only for the same-skill TXT linter: it revalidates the v2
    payload against its own six-field semantic section, but does not claim
    cross-branch lock proof.  Controller merge must use ``--locked-shot-map``.
    """

    if not isinstance(data, dict):
        return [Issue("TEXT_HANDOFF_TYPE_INVALID", "handoff 根节点必须是对象")]
    if locked_shot_map is not None and not isinstance(locked_shot_map, dict):
        return [Issue("TEXT_HANDOFF_LOCK_INVALID", "locked_shot_map 根节点必须是对象")]

    module = _load_director_validator()
    effective_lock = data if locked_shot_map is None else locked_shot_map
    errors, _contract = module.validate_handoff(data, effective_lock)
    issues = [Issue(_issue_code(str(message)), str(message)) for message in errors]

    # This entry point is text-only.  A structurally valid image handoff must
    # still be rejected here even though the director validator accepts it.
    if data.get("branch_role") != "text" and not any(
        issue.code == "TEXT_HANDOFF_ROLE_INVALID" for issue in issues
    ):
        issues.insert(0, Issue("TEXT_HANDOFF_ROLE_INVALID", "branch_role 必须使用机器值 text"))
    if data.get("schema_version") != SCHEMA_VERSION and not any(
        issue.code == "TEXT_HANDOFF_VERSION_INVALID" for issue in issues
    ):
        issues.insert(
            0,
            Issue(
                "TEXT_HANDOFF_VERSION_INVALID",
                f"schema_version 必须为 {SCHEMA_VERSION}；旧 v1 禁止合并",
            ),
        )

    unique: list[Issue] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        key = (issue.code, issue.message)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


def _load_object(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} 根节点必须是对象：{path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="使用 director canonical gate 校验 text-handoff-v2.0",
    )
    parser.add_argument("handoff", type=Path)
    parser.add_argument(
        "--locked-shot-map",
        required=True,
        type=Path,
        help="总控派发的只读 locked_shot_map.json；v2 必填",
    )
    parser.add_argument(
        "--print-shot-map-sha256",
        action="store_true",
        help="打印锁定六项语义载荷的 canonical SHA-256",
    )
    args = parser.parse_args()

    try:
        data = _load_object(args.handoff.expanduser().resolve(), "handoff")
        locked = _load_object(args.locked_shot_map.expanduser().resolve(), "locked_shot_map")
        if args.print_shot_map_sha256:
            print(compute_shot_map_sha256(locked))
        issues = validate_text_handoff(data, locked_shot_map=locked)
    except (OSError, ValueError, json.JSONDecodeError, ImportError) as exc:
        print(json.dumps({"status": "blocked", "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 2

    result = {
        "schema_version": data.get("schema_version"),
        "branch_role": data.get("branch_role"),
        "status": "valid" if not issues else "blocked",
        "error_count": len(issues),
        "errors": [{"code": item.code, "message": item.message} for item in issues],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
