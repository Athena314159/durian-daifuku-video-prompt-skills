#!/usr/bin/env python3
"""Initialize a versioned Jimeng video-remix project from the bundled template."""

from __future__ import annotations

import argparse
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


def require_profile(profile_id: str) -> Path:
    profile_path = PROFILES_DIR / f"{profile_id}.json"
    if not profile_path.is_file():
        available = ", ".join(sorted(path.stem for path in PROFILES_DIR.glob("*.json")))
        raise ValueError(f"Unknown profile '{profile_id}'. Available profiles: {available}")
    return profile_path


def initialize_project(
    name: str,
    output: Path,
    product_profile: str,
    style_profile: str,
    project_id: Optional[str] = None,
) -> Path:
    if not TEMPLATE_DIR.is_dir():
        raise FileNotFoundError(f"Missing project template: {TEMPLATE_DIR}")

    product_path = require_profile(product_profile)
    style_path = require_profile(style_profile)

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
    project.update(
        {
            "project_id": resolved_id,
            "project_name": name,
            "product_profile": product_profile,
            "style_profile": style_profile,
            "created_at": created_at,
            "updated_at": created_at,
        }
    )
    write_json(project_dir / "project.json", project)

    shutil.copy2(product_path, project_dir / "library" / "product_bible.json")
    shutil.copy2(style_path, project_dir / "library" / "style_bible.json")
    return project_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a non-destructive Jimeng video-remix project."
    )
    parser.add_argument("--name", required=True, help="Human-readable project name.")
    parser.add_argument("--output", required=True, type=Path, help="Directory that will contain the project.")
    parser.add_argument(
        "--product-profile",
        default="durian-daifuku-v1",
        help="Bundled product profile id, without .json.",
    )
    parser.add_argument(
        "--style-profile",
        default="ugc-food-review-v1",
        help="Bundled style profile id, without .json.",
    )
    parser.add_argument("--project-id", help="Optional stable directory/project id.")
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
        )
    except (FileExistsError, FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(project_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
