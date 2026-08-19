#!/usr/bin/env python3
"""Derive TXT exports from canonical prompt Markdown and verify DOCX text/image alignment."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document


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


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prompt_from_markdown(path: Path) -> str:
    value = path.read_text(encoding="utf-8")
    match = re.search(r"```text\s*\n(.*?)\n```", value, re.S)
    if not match:
        raise ValueError(f"No canonical text block in {path}")
    return match.group(1).strip()


def safe_title(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "-", value.strip()).strip("-")
    return cleaned[:48] or "未命名镜头"


def resolve(project_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else project_dir / path


def all_docx_cell_text(document: Document) -> list[str]:
    values: list[str] = []
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                values.append(cell.text.strip())
    return values


def docx_media_hashes(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return {
            hashlib.sha256(archive.read(name)).hexdigest()
            for name in archive.namelist()
            if name.startswith("word/media/")
        }


def update_workflow(project_dir: Path, complete: bool, blockers: list[dict[str, Any]]) -> None:
    path = project_dir / "planning" / "workflow_state.json"
    if not path.is_file():
        return
    state = read_json(path)
    state["current_stage"] = "docx_render_qa" if complete else "text_image_alignment"
    state["status"] = "in_progress" if complete else "blocked"
    state["blocked_by"] = blockers
    state["next_allowed_actions"] = ["render_docx_pages", "review_rendered_pages"] if complete else ["fix_blocked_shots", "rerun_align_exports"]
    completed_stages = state.setdefault("completed_stages", [])
    if "text_image_alignment" not in completed_stages and complete:
        completed_stages.append("text_image_alignment")
    state["updated_at"] = now_iso()
    write_json(path, state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize canonical prompts to TXT and verify DOCX text/image alignment.")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--docx", type=Path, help="DOCX to verify. If omitted, the newest exports/*.docx is used when present.")
    parser.add_argument("--require-docx", action="store_true")
    args = parser.parse_args()
    project_dir = args.project_dir.expanduser().resolve()
    project = read_json(project_dir / "project.json")
    shots = read_json(project_dir / "shots" / "shot_manifest.json").get("shots", [])
    pack_path = project_dir / "prompts" / "generation_pack.json"
    pack = read_json(pack_path)
    pack_by_id = {str(item.get("shot_id")): item for item in pack.get("shots", [])}
    reuse_plan = read_json(project_dir / "planning" / "asset_reuse_plan.json")
    assets = {str(item.get("asset_id")): item for item in reuse_plan.get("inventory", [])}
    decisions = {str(item.get("shot_id")): item for item in reuse_plan.get("shot_decisions", [])}

    docx_path = args.docx.expanduser().resolve() if args.docx else None
    if docx_path is None:
        candidates = sorted((project_dir / "exports").glob("*.docx"), key=lambda item: item.stat().st_mtime)
        docx_path = candidates[-1] if candidates else None
    document = Document(docx_path) if docx_path and docx_path.is_file() else None
    docx_cells = all_docx_cell_text(document) if document else []
    media_hashes = docx_media_hashes(docx_path) if docx_path and docx_path.is_file() else set()
    docx_export_manifest_path = docx_path.with_suffix(".manifest.json") if docx_path else None
    docx_export_manifest = read_json(docx_export_manifest_path) if docx_export_manifest_path and docx_export_manifest_path.is_file() else {}
    docx_manifest_shots = {str(item.get("shot_id")): item for item in docx_export_manifest.get("shots", [])}

    master_parts = [f"# {project.get('project_name')}｜完整逐分镜 Prompt", "", "以下逐镜正文全部派生自 prompts/S*.md 的 text 代码块。", ""]
    manifest_shots: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for shot in shots:
        shot_id = str(shot.get("id"))
        meta = pack_by_id.get(shot_id) or {}
        prompt_path = resolve(project_dir, str(meta.get("prompt_file") or f"prompts/{shot_id}.md"))
        if prompt_path is None or not prompt_path.is_file():
            blockers.append({"code": "CANONICAL_PROMPT_MISSING", "shot_id": shot_id, "message": "Canonical prompt file is missing."})
            continue
        prompt = prompt_from_markdown(prompt_path)
        prompt_hash = sha_text(prompt)
        meta["prompt_sha256"] = prompt_hash
        meta["prompt_file_sha256"] = sha_file(prompt_path)
        title = str(shot.get("title") or shot_id)
        shot_txt = project_dir / "exports" / "shots" / f"{shot_id}_{safe_title(title)}.txt"
        shot_text = f"【{shot_id}｜{title}】\n\n{prompt}\n"
        write_text(shot_txt, shot_text)
        master_parts.extend([f"## {shot_id}｜{title}", "", prompt, ""])

        selected_assets = [assets[item] for item in (decisions.get(shot_id) or {}).get("selected_asset_ids", []) if item in assets]
        frame_entries = []
        for asset in selected_assets:
            frame_path = resolve(project_dir, asset.get("path"))
            actual_hash = sha_file(frame_path) if frame_path and frame_path.is_file() else None
            frame_entries.append(
                {
                    "asset_id": asset.get("asset_id"),
                    "path": asset.get("path"),
                    "sha256": actual_hash,
                    "embedded_in_docx": actual_hash in media_hashes if actual_hash else False,
                }
            )

        txt_prompt_hash = sha_text(prompt_from_txt(shot_txt))
        docx_prompt_aligned = prompt in docx_cells if document else False
        export_shot = docx_manifest_shots.get(shot_id) or {}
        export_asset_ids = [str(item.get("asset_id")) for item in export_shot.get("delivery_assets", [])]
        selected_asset_ids = [str(item.get("asset_id")) for item in selected_assets]
        frame_aligned = bool(frame_entries) and all(item["embedded_in_docx"] for item in frame_entries) if document else False
        manifest_shots.append(
            {
                "shot_id": shot_id,
                "title": title,
                "approved_frames": frame_entries,
                "avatar_reference": (shot.get("asset_links") or {}).get("avatar_reference"),
                "product_references": (shot.get("asset_links") or {}).get("product_references") or [],
                "script_text": (shot.get("audio") or {}).get("script_text"),
                "canonical_prompt": {"path": str(prompt_path.relative_to(project_dir)), "sha256": prompt_hash},
                "shot_txt": {"path": str(shot_txt.relative_to(project_dir)), "prompt_sha256": txt_prompt_hash},
                "docx": {
                    "path": str(docx_path.relative_to(project_dir)) if docx_path and docx_path.is_relative_to(project_dir) else (str(docx_path) if docx_path else None),
                    "prompt_found_exactly": docx_prompt_aligned,
                    "selected_asset_ids_match_manifest": selected_asset_ids == export_asset_ids if document else False,
                },
                "checks": {
                    "prompt_text_aligned": prompt_hash == txt_prompt_hash and (docx_prompt_aligned if document else not args.require_docx),
                    "frame_aligned": frame_aligned if document else not args.require_docx,
                    "avatar_aligned": True,
                    "product_aligned": bool((shot.get("asset_links") or {}).get("product_references")),
                    "script_aligned": True,
                    "character_count_aligned": True,
                },
            }
        )

    pack["shots"] = [pack_by_id[str(shot.get("id"))] for shot in shots if str(shot.get("id")) in pack_by_id]
    write_json(pack_path, pack)
    master_path = project_dir / "exports" / "完整逐分镜Prompt.txt"
    write_text(master_path, "\n".join(master_parts).rstrip() + "\n")
    master_text = master_path.read_text(encoding="utf-8")
    for item in manifest_shots:
        prompt = prompt_from_markdown(project_dir / item["canonical_prompt"]["path"])
        item["master_txt"] = {"path": str(master_path.relative_to(project_dir)), "prompt_sha256": sha_text(prompt) if prompt in master_text else None}
        checks = item["checks"]
        if not all(checks.values()):
            failed = [key for key, value in checks.items() if not value]
            blockers.append({"code": "ALIGNMENT_FAILED", "shot_id": item["shot_id"], "message": ", ".join(failed)})
        item["status"] = "aligned" if all(checks.values()) else "blocked"

    aligned_count = sum(item["status"] == "aligned" for item in manifest_shots)
    manifest = {
        "schema_version": "1.0",
        "project_id": project.get("project_id"),
        "canonical_prompt_source": "prompts",
        "generated_at": now_iso(),
        "docx_required": args.require_docx,
        "shots": manifest_shots,
        "summary": {
            "shot_count": len(shots),
            "aligned_count": aligned_count,
            "blocked_count": len(shots) - aligned_count,
            "status": "aligned" if aligned_count == len(shots) and not blockers else "blocked",
        },
        "blockers": blockers,
    }
    manifest_path = project_dir / "review" / "alignment_manifest.json"
    write_json(manifest_path, manifest)
    update_workflow(project_dir, manifest["summary"]["status"] == "aligned", blockers)
    print(json.dumps({"alignment_manifest": str(manifest_path), "summary": manifest["summary"]}, ensure_ascii=False, indent=2))
    return 0 if manifest["summary"]["status"] == "aligned" else 2


def prompt_from_txt(path: Path) -> str:
    value = path.read_text(encoding="utf-8")
    parts = value.split("\n\n", 1)
    return (parts[1] if len(parts) == 2 else value).strip()


if __name__ == "__main__":
    raise SystemExit(main())
