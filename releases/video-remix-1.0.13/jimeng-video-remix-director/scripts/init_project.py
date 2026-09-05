#!/usr/bin/env python3
"""Initialize a versioned Jimeng video-remix project from the bundled template."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = SKILL_DIR / "assets" / "project-template"
PROFILES_DIR = SKILL_DIR / "assets" / "profiles"
RELEASE_MANIFEST_PATH = SKILL_DIR / "references" / "skill-release.json"
VALID_EXECUTION_TIERS = ("source_intake", "diagnose_only", "first_frame_only", "prompt_only", "full_delivery")


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized[:40] or f"project-{uuid.uuid4().hex[:8]}"


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(path)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed_product_knowledge_and_assets(
    project_dir: Path,
    product_profile_data: Dict[str, Any],
) -> list[Dict[str, Any]]:
    """Copy approved product references and seed scoped knowledge for a new project."""
    profile_id = str(product_profile_data.get("profile_id") or "").strip()
    if not profile_id:
        raise ValueError("Product profile must define profile_id before seeding knowledge")

    copied_assets: list[Dict[str, Any]] = []
    image_entries: list[Dict[str, Any]] = []
    for index, asset in enumerate(product_profile_data.get("reference_assets") or []):
        if not isinstance(asset, dict) or asset.get("approved") is not True:
            continue
        asset_id = str(asset.get("id") or "").strip()
        source_relative = str(asset.get("source_path") or "").strip()
        target_relative = str(asset.get("target_path") or "").strip()
        role = str(asset.get("role") or "").strip()
        if not all((asset_id, source_relative, target_relative, role)):
            raise ValueError(f"Approved reference_assets[{index}] requires id/source_path/target_path/role")
        source_path = (SKILL_DIR / source_relative).resolve()
        if not source_path.is_file() or SKILL_DIR.resolve() not in source_path.parents:
            raise ValueError(f"Approved product reference is missing or outside the Skill: {source_relative}")
        expected_sha256 = str(asset.get("sha256") or "").strip()
        if expected_sha256 and digest(source_path) != expected_sha256:
            raise ValueError(f"Approved product reference hash mismatch: {asset_id}")
        target_path = (project_dir / target_relative).resolve()
        if project_dir.resolve() not in target_path.parents:
            raise ValueError(f"Product reference target must stay inside the project: {target_relative}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        copied = dict(asset)
        copied.pop("source_path", None)
        copied["path"] = target_relative
        copied["sha256"] = digest(target_path)
        copied_assets.append(copied)

        allowed_states = [str(value) for value in asset.get("allowed_states") or [] if str(value).strip()]
        applies_to: Dict[str, Any] = {"product_profile": profile_id}
        if allowed_states and "*" not in allowed_states:
            applies_to["product_state"] = allowed_states
        image_entries.append(
            {
                "id": f"KB-{asset_id}",
                "type": "image",
                "title": asset_id,
                "path": target_relative,
                "sha256": copied["sha256"],
                "reference_role": role,
                "allowed_inheritance": asset.get("allowed_inheritance") or [],
                "forbidden_inheritance": asset.get("forbidden_inheritance") or [],
                "applies_to": applies_to,
                "priority": 95,
                "approved": True,
                "version": int(product_profile_data.get("version") or 1),
            }
        )

    knowledge_path = project_dir / "library" / "knowledge_index.json"
    knowledge = load_json(knowledge_path)
    seed_entries = product_profile_data.get("knowledge_seed") or []
    if not isinstance(seed_entries, list):
        raise ValueError("Product profile knowledge_seed must be a list")
    entries = knowledge.get("entries") or []
    if not isinstance(entries, list):
        raise ValueError("Project knowledge_index entries must be a list")
    combined = [*entries, *seed_entries, *image_entries]
    ids = [entry.get("id") for entry in combined if isinstance(entry, dict)]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Product knowledge seed contains duplicate ids for {profile_id}")
    knowledge["entries"] = combined
    knowledge["version"] = max(int(knowledge.get("version") or 1), int(product_profile_data.get("version") or 1))
    write_json(knowledge_path, knowledge)
    return copied_assets


def require_profile(profile_id: str) -> Path:
    profile_path = PROFILES_DIR / f"{profile_id}.json"
    if not profile_path.is_file():
        available = ", ".join(sorted(path.stem for path in PROFILES_DIR.glob("*.json")))
        raise ValueError(f"Unknown profile '{profile_id}'. Available profiles: {available}")
    return profile_path


def initialize_project(
    name: str,
    output: Path,
    product_profile: Optional[str],
    style_profile: str,
    project_id: Optional[str] = None,
    product_mode: Optional[str] = None,
    prompt_length_enabled: bool = False,
    prompt_length_minimum: Optional[int] = None,
    prompt_length_maximum: Optional[int] = None,
    execution_tier: str = "source_intake",
) -> Path:
    if not TEMPLATE_DIR.is_dir():
        raise FileNotFoundError(f"Missing project template: {TEMPLATE_DIR}")
    if execution_tier not in VALID_EXECUTION_TIERS:
        raise ValueError(f"execution_tier must be one of {VALID_EXECUTION_TIERS}")

    resolved_product_mode = product_mode or ("replace_product" if product_profile else "preserve_source_product")
    if resolved_product_mode not in {"preserve_source_product", "replace_product"}:
        raise ValueError("product_mode must be preserve_source_product or replace_product")
    if resolved_product_mode == "preserve_source_product" and product_profile:
        raise ValueError("preserve_source_product cannot bind a target product profile")
    if resolved_product_mode == "replace_product" and not product_profile:
        raise ValueError("replace_product requires --product-profile")
    product_path = require_profile(product_profile) if product_profile else None
    style_path = require_profile(style_profile)
    product_profile_data = load_json(product_path) if product_path else None
    if product_profile_data is not None and product_profile_data.get("profile_id") != product_profile:
        raise ValueError(f"Product profile id mismatch in {product_path}: expected {product_profile!r}")
    profile_rule_overrides = (product_profile_data or {}).get("project_rule_overrides") or {}
    if not isinstance(profile_rule_overrides, dict):
        raise ValueError(f"Product profile project_rule_overrides must be an object: {product_path}")

    if not prompt_length_enabled and (prompt_length_minimum is not None or prompt_length_maximum is not None):
        raise ValueError("Prompt length bounds require --enable-prompt-length-contract; disabled means both gates are 0/off")
    if prompt_length_enabled:
        resolved_prompt_minimum = 3000 if prompt_length_minimum is None else prompt_length_minimum
        resolved_prompt_maximum = 4000 if prompt_length_maximum is None else prompt_length_maximum
        if (
            not isinstance(resolved_prompt_minimum, int)
            or isinstance(resolved_prompt_minimum, bool)
            or not isinstance(resolved_prompt_maximum, int)
            or isinstance(resolved_prompt_maximum, bool)
            or resolved_prompt_minimum < 1
            or resolved_prompt_maximum < resolved_prompt_minimum
        ):
            raise ValueError("Enabled Prompt length contract requires positive minimum/maximum bounds with maximum >= minimum")
    else:
        resolved_prompt_minimum = 0
        resolved_prompt_maximum = 0

    resolved_id = project_id or f"{datetime.now().strftime('%Y%m%d')}-{slugify(name)}"
    project_dir = output.expanduser().resolve() / resolved_id
    if project_dir.exists():
        raise FileExistsError(
            f"Project already exists: {project_dir}. Choose a different --project-id; existing projects are never overwritten."
        )

    shutil.copytree(TEMPLATE_DIR, project_dir)
    for directory in (
        project_dir / "source" / "analysis",
        project_dir / "prompts",
        project_dir / "exports",
        project_dir / "review",
        project_dir / "shots",
        project_dir / "library",
        project_dir / "planning",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    created_at = now_iso()
    project = load_json(project_dir / "project.json")
    release_manifest = load_json(RELEASE_MANIFEST_PATH)
    project.update(
        {
            "project_id": resolved_id,
            "project_name": name,
            "product_mode": resolved_product_mode,
            "product_profile": product_profile,
            "style_profile": style_profile,
            "execution_tier": execution_tier,
            "created_at": created_at,
            "updated_at": created_at,
        }
    )
    project["prompt_length_contract"] = {
        "enabled": prompt_length_enabled,
        "minimum_non_whitespace_characters": resolved_prompt_minimum,
        "maximum_non_whitespace_characters": resolved_prompt_maximum,
    }
    project["skill_release_lock"] = {
        "bundle_release_id": release_manifest["bundle_release_id"],
        "prompt_authoring_contract": release_manifest["prompt_authoring_contract"],
        "auto_upgrade": False,
    }
    if product_profile_data is not None:
        project_rules = project.setdefault("project_rules", {})
        for key, value in profile_rule_overrides.items():
            project_rules[key] = value
    write_json(project_dir / "project.json", project)

    reuse_path = project_dir / "planning" / "asset_reuse_plan.json"
    reuse_plan = load_json(reuse_path)
    reuse_plan["contract_binding"] = {
        "bundle_release_id": release_manifest["bundle_release_id"],
        "prompt_authoring_contract": release_manifest["prompt_authoring_contract"],
        "product_profile": product_profile,
    }
    write_json(reuse_path, reuse_plan)

    workflow_path = project_dir / "planning" / "workflow_state.json"
    workflow = load_json(workflow_path)
    workflow.update(
        {
            "project_id": resolved_id,
            "execution_tier": execution_tier,
            "skill_versions": {
                "jimeng-video-remix-director": {"sha256": digest(SKILL_DIR / "SKILL.md")},
                "bundle_release_id": release_manifest["bundle_release_id"],
                "prompt_authoring_contract": release_manifest["prompt_authoring_contract"],
                "product_profile": product_profile,
                "style_profile": style_profile,
            },
            "updated_at": created_at,
        }
    )
    write_json(workflow_path, workflow)

    candidates_path = project_dir / "planning" / "skill_update_candidates.json"
    candidates = load_json(candidates_path)
    candidates["project_id"] = resolved_id
    write_json(candidates_path, candidates)

    alignment_path = project_dir / "review" / "alignment_manifest.json"
    alignment = load_json(alignment_path)
    alignment["project_id"] = resolved_id
    write_json(alignment_path, alignment)

    shutil.copy2(style_path, project_dir / "library" / "style_bible.json")
    product_library_path = project_dir / "library" / "product_library.json"
    product_library = load_json(product_library_path)
    if product_path:
        shutil.copy2(product_path, project_dir / "library" / "product_bible.json")
        assert product_profile_data is not None
        seeded_reference_assets = seed_product_knowledge_and_assets(project_dir, product_profile_data)
        product_library["products"] = [
            {
                "id": product_profile,
                "name": product_profile_data.get("name", product_profile),
                "active": True,
                "rights_cleared": False,
                "usage_scope": "internal_test",
                "profile_path": "library/product_bible.json",
                "version": product_profile_data.get("version", 1),
                "states": sorted(((product_profile_data.get("state_profiles") or product_profile_data.get("states") or {})).keys()),
                "reference_assets": seeded_reference_assets,
                "approved_result_assets": [],
            }
        ]
    else:
        product_library["products"] = []
    write_json(product_library_path, product_library)
    return project_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a non-destructive Jimeng video-remix project."
    )
    parser.add_argument("--name", required=True, help="Human-readable project name.")
    parser.add_argument("--output", required=True, type=Path, help="Directory that will contain the project.")
    parser.add_argument(
        "--product-profile",
        help="Explicit target product profile id, without .json. Omit to preserve the source product.",
    )
    parser.add_argument(
        "--product-mode",
        choices=("preserve_source_product", "replace_product"),
        help="Defaults to preserve_source_product unless --product-profile explicitly selects a target.",
    )
    parser.add_argument(
        "--style-profile",
        default="ugc-food-review-v1",
        help="Bundled style profile id, without .json.",
    )
    parser.add_argument(
        "--enable-prompt-length-contract",
        action="store_true",
        help="Enable both Prompt length gates; defaults to 3000–4000 unless both custom bounds are supplied.",
    )
    parser.add_argument(
        "--prompt-length-minimum",
        type=int,
        help="Custom enabled minimum non-whitespace Prompt characters.",
    )
    parser.add_argument(
        "--prompt-length-maximum",
        type=int,
        help="Custom enabled maximum non-whitespace Prompt characters.",
    )
    parser.add_argument("--project-id", help="Optional stable directory/project id.")
    parser.add_argument(
        "--execution-tier",
        choices=VALID_EXECUTION_TIERS,
        default="source_intake",
        help="Canonical workflow tier; only prompt_only and full_delivery may compile deliverable Prompts.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        project_dir = initialize_project(
            name=args.name,
            output=args.output,
            product_profile=args.product_profile,
            style_profile=args.style_profile,
            project_id=args.project_id,
            product_mode=args.product_mode,
            prompt_length_enabled=args.enable_prompt_length_contract,
            prompt_length_minimum=args.prompt_length_minimum,
            prompt_length_maximum=args.prompt_length_maximum,
            execution_tier=args.execution_tier,
        )
    except (FileExistsError, FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(project_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
