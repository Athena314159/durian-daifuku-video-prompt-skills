#!/usr/bin/env python3
"""Read-only final audit of canonical inputs, compile snapshot and storyboard DOCX.

The script writes only review/alignment_manifest.json and workflow status.  It
does not rewrite generation_pack.json, canonical prompts, TXT files, images or
the DOCX under review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from export_jimeng_docx import (
    clock_label,
    delivery_asset_responsibility,
    exact_time_label,
    format_break_occurrence,
    format_eating_occurrence,
    format_package_face,
    format_package_face_for_word,
    format_performance_layers,
    has_complete_performance_layers,
    matching_break_occurrences,
    matching_eating_occurrences,
    storyboard_units,
    target_frame_caption,
    unit_id,
    validate_compile_snapshot,
)
from pipeline import canonical_input_hashes, normalized_prompt_length_contract, normalized_skill_release_lock


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": W, "a": A, "r": R, "pr": PKG_REL}
SHOT_ID_RE = re.compile(r"S\d+")
UNIT_ID_RE = re.compile(r"(?:SRC|ADD)\d+")
SHOT_HEADING_RE = re.compile(r"^(S\d+)(?:｜|$)")
UNIT_HEADING_RE = re.compile(r"^((?:SRC|ADD)\d+)(?:｜|$)")
FRAME_CAPTION_RE = re.compile(
    r"^((?:SRC|ADD)\d+)｜([^｜]+)｜已批准(｜连续边界参考)?｜职责：(.+)$"
)


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


def resolve(project_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else project_dir / path


def paragraph_style(element: ET.Element) -> str | None:
    style = element.find("./w:pPr/w:pStyle", NS)
    return style.get(f"{{{W}}}val") if style is not None else None


def element_text(element: ET.Element) -> str:
    # python-docx serializes embedded newlines as w:br rather than as literal
    # text.  Preserve them so Prompt comparison is genuinely byte-for-byte at
    # the editable-text level, not a whitespace-flattened approximation.
    values: list[str] = []
    for node in element.iter():
        if node.tag == f"{{{W}}}t":
            values.append(node.text or "")
        elif node.tag in {f"{{{W}}}br", f"{{{W}}}cr"}:
            values.append("\n")
        elif node.tag == f"{{{W}}}tab":
            values.append("\t")
    return "".join(values).strip()


def parse_docx_body(path: Path) -> dict[str, Any]:
    """Map the golden segment/action-card/target-frame structure from OOXML."""
    with zipfile.ZipFile(path) as archive:
        required = {"[Content_Types].xml", "word/document.xml", "word/_rels/document.xml.rels"}
        missing = required.difference(archive.namelist())
        if missing:
            raise ValueError(f"DOCX OPC parts missing: {', '.join(sorted(missing))}")
        document_root = ET.fromstring(archive.read("word/document.xml"))
        relationships_root = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
        relationship_targets: dict[str, str] = {}
        for relationship in relationships_root.findall("pr:Relationship", NS):
            if relationship.get("TargetMode") == "External":
                continue
            rel_id = relationship.get("Id")
            target = relationship.get("Target")
            if not rel_id or not target:
                continue
            if target.startswith("/"):
                member = target.lstrip("/")
            else:
                member = posixpath.normpath(posixpath.join("word", target))
            relationship_targets[rel_id] = member

        body = document_root.find("w:body", NS)
        if body is None:
            raise ValueError("word/document.xml has no w:body")
        shot_order: list[str] = []
        unit_order: list[str] = []
        duplicate_shot_ids: list[str] = []
        duplicate_unit_ids: list[str] = []
        shots: dict[str, dict[str, Any]] = {}
        units: dict[str, dict[str, Any]] = {}
        current_shot: str | None = None
        current_unit: str | None = None
        current_section: str | None = None
        all_body_image_relationship_count = 0

        for child in list(body):
            text = element_text(child)
            if child.tag == f"{{{W}}}p":
                style = paragraph_style(child)
                shot_match = SHOT_HEADING_RE.match(text) if style == "Heading1" else None
                unit_match = UNIT_HEADING_RE.match(text) if style in {"Heading2", "Heading3"} else None
                if shot_match:
                    shot_id = shot_match.group(1)
                    if shot_id in shots:
                        duplicate_shot_ids.append(shot_id)
                    else:
                        shot_order.append(shot_id)
                        shots[shot_id] = {
                            "texts": [],
                            "unit_order": [],
                            "heading_text": text,
                            "continuity_boundary_references": [],
                        }
                    current_shot = shot_id
                    current_unit = None
                    current_section = None
                elif current_shot and style == "Heading2" and text == "动作镜头对应":
                    current_section = "action_cards"
                    current_unit = None
                elif current_shot and style == "Heading2" and text == "目标帧与职责":
                    current_section = "target_frames"
                    current_unit = None
                elif current_shot and style == "Heading2" and text == "可复制Prompt原文":
                    current_section = "prompt"
                    current_unit = None
                elif current_shot and unit_match and current_section == "action_cards":
                    unit_value = unit_match.group(1)
                    if unit_value in units:
                        duplicate_unit_ids.append(unit_value)
                    else:
                        unit_order.append(unit_value)
                        units[unit_value] = {
                            "shot_id": current_shot,
                            "heading_text": text,
                            "texts": [],
                            "frame_entries": [],
                            "image_hashes": [],
                            "image_parts": [],
                        }
                        if current_shot in shots:
                            shots[current_shot]["unit_order"].append(unit_value)
                    current_unit = unit_value

            if current_shot in shots and text:
                shots[current_shot]["texts"].append(text)
            if current_unit in units and text and current_section == "action_cards":
                units[current_unit]["texts"].append(text)

            if child.tag == f"{{{W}}}tbl":
                cells = child.findall(".//w:tc", NS)
                for cell in cells:
                    blips = cell.findall(".//a:blip", NS)
                    all_body_image_relationship_count += len(blips)
                    if not blips:
                        continue
                    cell_text = element_text(cell)
                    caption_match = FRAME_CAPTION_RE.search(cell_text)
                    for blip in blips:
                        rel_id = blip.get(f"{{{R}}}embed")
                        member = relationship_targets.get(rel_id or "")
                        if not member or member not in archive.namelist() or caption_match is None:
                            continue
                        owner_unit_id, asset_id, continuity_marker, responsibility = caption_match.groups()
                        image_hash = hashlib.sha256(archive.read(member)).hexdigest()
                        entry = {
                            "owner_unit_id": owner_unit_id,
                            "asset_id": asset_id,
                            "caption": caption_match.group(0),
                            "responsibility": responsibility,
                            "image_part": member,
                            "image_sha256": image_hash,
                            "continuity_boundary_reference": bool(continuity_marker),
                            "display_shot_id": current_shot,
                        }
                        if continuity_marker:
                            if current_shot in shots:
                                shots[current_shot]["continuity_boundary_references"].append(entry)
                        elif owner_unit_id in units:
                            units[owner_unit_id]["frame_entries"].append(entry)
                            units[owner_unit_id]["image_parts"].append(member)
                            units[owner_unit_id]["image_hashes"].append(image_hash)
            else:
                all_body_image_relationship_count += len(child.findall(".//a:blip", NS))

        return {
            "shot_order": shot_order,
            "unit_order": unit_order,
            "duplicate_shot_ids": duplicate_shot_ids,
            "duplicate_unit_ids": duplicate_unit_ids,
            "shots": shots,
            "units": units,
            "owner_frame_image_relationship_count": sum(len(item["image_parts"]) for item in units.values()),
            "continuity_boundary_reference_count": sum(
                len(item["continuity_boundary_references"]) for item in shots.values()
            ),
            "all_body_image_relationship_count": all_body_image_relationship_count,
            "embedded_media_count": len([name for name in archive.namelist() if name.startswith("word/media/")]),
        }


def update_workflow(project_dir: Path, complete: bool, blockers: list[dict[str, Any]]) -> None:
    path = project_dir / "planning" / "workflow_state.json"
    if not path.is_file():
        return
    state = read_json(path)
    state["current_stage"] = "docx_render_qa" if complete else "text_image_alignment"
    state["status"] = "in_progress" if complete else "blocked"
    state["blocked_by"] = blockers
    state["next_allowed_actions"] = (
        ["render_docx_pages", "review_rendered_pages"] if complete else ["fix_blocked_units", "recompile_if_stale", "rerun_docx_export", "rerun_alignment_audit"]
    )
    completed_stages = state.setdefault("completed_stages", [])
    if complete and "text_image_alignment" not in completed_stages:
        completed_stages.append("text_image_alignment")
    state["updated_at"] = now_iso()
    write_json(path, state)


def exact_label(label: str, value: Any) -> str:
    return f"{label}：{value}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only audit of a final Jimeng storyboard DOCX.")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--docx", type=Path, help="DOCX to audit; defaults to newest exports/*.docx.")
    parser.add_argument("--export-manifest", type=Path, help="Internal manifest created by export_jimeng_docx.py.")
    parser.add_argument("--require-docx", action="store_true")
    args = parser.parse_args()
    project_dir = args.project_dir.expanduser().resolve()

    project = read_json(project_dir / "project.json")
    shot_manifest = read_json(project_dir / "shots" / "shot_manifest.json")
    shots = shot_manifest.get("shots") or []
    if not isinstance(shots, list):
        raise ValueError("shots/shot_manifest.json.shots must be a list")
    story = read_json(project_dir / "planning" / "story_plan.json")
    pack_path = project_dir / "prompts" / "generation_pack.json"
    pack = read_json(pack_path)
    pack_by_id = {str(item.get("shot_id")): item for item in (pack.get("shots") or []) if isinstance(item, dict)}
    reuse_plan = read_json(project_dir / "planning" / "asset_reuse_plan.json")
    assets = {str(item.get("asset_id")): item for item in (reuse_plan.get("inventory") or []) if isinstance(item, dict)}
    decisions = {str(item.get("shot_id")): item for item in (reuse_plan.get("shot_decisions") or []) if isinstance(item, dict)}

    docx_path = args.docx.expanduser().resolve() if args.docx else None
    if docx_path is None:
        candidates = sorted((project_dir / "exports").glob("*.docx"), key=lambda item: item.stat().st_mtime)
        docx_path = candidates[-1] if candidates else None
    if args.require_docx and (docx_path is None or not docx_path.is_file()):
        docx_path = None

    export_manifest_path = args.export_manifest.expanduser().resolve() if args.export_manifest else None
    if export_manifest_path is None and docx_path:
        adjacent = docx_path.with_suffix(".manifest.json")
        internal = project_dir / "review" / f"{docx_path.stem}.manifest.json"
        export_manifest_path = adjacent if adjacent.is_file() else internal
    export_manifest = read_json(export_manifest_path) if export_manifest_path and export_manifest_path.is_file() else {}
    export_shots = {str(item.get("shot_id")): item for item in (export_manifest.get("shots") or []) if isinstance(item, dict)}

    blockers: list[dict[str, Any]] = []

    def block(code: str, message: str, shot_id: str | None = None, unit: str | None = None) -> None:
        value: dict[str, Any] = {"code": code, "message": message}
        if shot_id:
            value["shot_id"] = shot_id
        if unit:
            value["unit_id"] = unit
        blockers.append(value)

    current_hashes = canonical_input_hashes(project_dir)
    project_contract = normalized_prompt_length_contract(project)
    project_release_lock = normalized_skill_release_lock(project)
    history_dir = resolve(project_dir, pack.get("history_dir"))
    history_pack_path = history_dir / "generation_pack.json" if history_dir else None
    snapshot_path = history_dir / "input_snapshot.json" if history_dir else None
    snapshot = read_json(snapshot_path) if snapshot_path and snapshot_path.is_file() else {}
    history_pack = read_json(history_pack_path) if history_pack_path and history_pack_path.is_file() else {}

    try:
        validated_snapshot, validated_snapshot_path, compile_snapshot_errors = validate_compile_snapshot(
            project_dir, project, shots, pack
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        validated_snapshot, validated_snapshot_path, compile_snapshot_errors = {}, snapshot_path, [str(exc)]
    for error in compile_snapshot_errors:
        block("COMPILE_SNAPSHOT_INVALID", error)

    global_checks = {
        "compile_snapshot_valid": not compile_snapshot_errors,
        "canonical_input_hashes_match_compile": pack.get("canonical_input_hashes") == current_hashes,
        "active_pack_matches_history": bool(history_pack) and history_pack == pack,
        "input_snapshot_compile_id_matches": bool(snapshot) and snapshot.get("compile_id") == pack.get("compile_id"),
        "input_snapshot_hashes_match": bool(snapshot) and snapshot.get("canonical_input_hashes") == current_hashes,
        "prompt_length_contract_matches": pack.get("prompt_length_contract") == project_contract,
        "skill_release_lock_matches": (
            pack.get("skill_release_lock") == project_release_lock
            or (
                project_release_lock.get("bundle_release_id") == "unmanaged-legacy"
                and pack.get("skill_release_lock") is None
            )
        ),
        "export_manifest_present": bool(export_manifest),
        "export_compile_id_matches": bool(export_manifest) and export_manifest.get("compile_id") == pack.get("compile_id"),
        "export_input_hashes_match": bool(export_manifest) and export_manifest.get("canonical_input_hashes") == current_hashes,
        "export_prompt_length_contract_matches": bool(export_manifest)
        and export_manifest.get("prompt_length_contract") == project_contract,
        "export_skill_release_lock_matches": bool(export_manifest)
        and (
            export_manifest.get("skill_release_lock") == project_release_lock
            or (
                project_release_lock.get("bundle_release_id") == "unmanaged-legacy"
                and export_manifest.get("skill_release_lock") is None
            )
        ),
        "export_pack_hash_matches": bool(export_manifest) and export_manifest.get("generation_pack_sha256") == sha_file(pack_path),
        "export_snapshot_hash_matches": bool(export_manifest)
        and validated_snapshot_path is not None
        and validated_snapshot_path.is_file()
        and export_manifest.get("input_snapshot_sha256") == sha_file(validated_snapshot_path),
        "export_project_id_matches": bool(export_manifest) and export_manifest.get("project_id") == project.get("project_id"),
        "export_source_sha256_matches": bool(export_manifest)
        and export_manifest.get("source_sha256") == pack.get("source_sha256"),
        "export_compile_snapshot_was_validated": bool(export_manifest)
        and export_manifest.get("compile_snapshot_validated") is True,
    }

    parsed_docx: dict[str, Any] | None = None
    if docx_path and docx_path.is_file():
        try:
            parsed_docx = parse_docx_body(docx_path)
        except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError) as exc:
            block("DOCX_INVALID", str(exc))
        global_checks["docx_hash_matches_export"] = bool(export_manifest) and export_manifest.get("docx_sha256") == sha_file(docx_path)
    else:
        global_checks["docx_hash_matches_export"] = not args.require_docx
        if args.require_docx:
            block("DOCX_REQUIRED", "Final DOCX is missing.")

    expected_shot_order = [str(shot.get("id")) for shot in shots]
    expected_unit_order = [unit_id(unit) for shot in shots for _, unit in storyboard_units(shot)]
    canonical_unit_asset_lists = [
        [str(value) for value in unit.get("delivery_asset_ids", [])]
        for shot in shots
        for _, unit in storyboard_units(shot)
    ]
    canonical_unit_asset_ids = [value for values in canonical_unit_asset_lists for value in values]
    canonical_owner_asset_paths: list[str] = []
    canonical_owner_asset_hashes: list[str] = []
    for shot in shots:
        for _, unit in storyboard_units(shot):
            for asset_id in unit.get("delivery_asset_ids") or []:
                asset = assets.get(str(asset_id)) or {}
                asset_path = resolve(project_dir, asset.get("path"))
                if asset_path and asset_path.is_file():
                    canonical_owner_asset_paths.append(str(asset_path.resolve()))
                    canonical_owner_asset_hashes.append(sha_file(asset_path))
    export_shot_order = [str(item.get("shot_id")) for item in (export_manifest.get("shots") or []) if isinstance(item, dict)]
    global_checks["generation_pack_shot_order_matches"] = [
        str(item.get("shot_id")) for item in (pack.get("shots") or []) if isinstance(item, dict)
    ] == expected_shot_order
    global_checks["export_shot_order_matches"] = export_shot_order == expected_shot_order
    global_checks["export_unit_order_matches"] = export_manifest.get("unit_order") == expected_unit_order
    global_checks["export_expected_image_count_matches_owner_frames"] = (
        export_manifest.get("expected_word_image_count") == len(canonical_unit_asset_ids)
    )
    global_checks["reuse_expected_image_count_matches_owner_frames"] = (
        (reuse_plan.get("summary") or {}).get("expected_word_image_count") == len(canonical_unit_asset_ids)
    )
    global_checks["canonical_units_have_at_least_one_asset_each"] = all(canonical_unit_asset_lists)
    global_checks["canonical_owner_asset_ids_are_cross_unit_unique"] = (
        len(set(canonical_unit_asset_ids)) == len(canonical_unit_asset_ids)
    )
    global_checks["canonical_owner_asset_paths_are_cross_unit_unique"] = (
        len(canonical_owner_asset_paths) == len(canonical_unit_asset_ids)
        and len(set(canonical_owner_asset_paths)) == len(canonical_owner_asset_paths)
    )
    global_checks["canonical_owner_asset_hashes_are_cross_unit_unique"] = (
        len(canonical_owner_asset_hashes) == len(canonical_unit_asset_ids)
        and len(set(canonical_owner_asset_hashes)) == len(canonical_owner_asset_hashes)
    )
    expected_continuity_count = sum(
        len(shot.get("continuity_boundary_references") or []) for shot in shots
    )
    if parsed_docx:
        global_checks.update(
            {
                "docx_shot_order_matches": parsed_docx["shot_order"] == expected_shot_order,
                "docx_unit_order_matches": parsed_docx["unit_order"] == expected_unit_order,
                "docx_has_no_duplicate_shot_headings": not parsed_docx["duplicate_shot_ids"],
                "docx_has_no_duplicate_unit_headings": not parsed_docx["duplicate_unit_ids"],
                "docx_owner_frame_relationship_count_matches_assets": (
                    parsed_docx["owner_frame_image_relationship_count"] == len(canonical_unit_asset_ids)
                ),
                "docx_continuity_reference_count_matches_canonical": (
                    parsed_docx["continuity_boundary_reference_count"] == expected_continuity_count
                ),
                "docx_all_body_image_occurrences_match_export": (
                    parsed_docx["all_body_image_relationship_count"]
                    == export_manifest.get("body_image_relationship_count")
                    == len(canonical_unit_asset_ids) + expected_continuity_count
                ),
                "docx_embedded_media_count_matches_export": (
                    parsed_docx["embedded_media_count"] == export_manifest.get("embedded_media_count")
                ),
            }
        )
    elif args.require_docx:
        global_checks.update(
            {
                "docx_shot_order_matches": False,
                "docx_unit_order_matches": False,
                "docx_has_no_duplicate_shot_headings": False,
                "docx_has_no_duplicate_unit_headings": False,
                "docx_owner_frame_relationship_count_matches_assets": False,
                "docx_continuity_reference_count_matches_canonical": False,
                "docx_all_body_image_occurrences_match_export": False,
                "docx_embedded_media_count_matches_export": False,
            }
        )

    for name, passed in global_checks.items():
        if not passed:
            block("GLOBAL_ALIGNMENT_FAILED", name)

    def shot_selected_assets(shot_id: str) -> list[dict[str, Any]]:
        return [assets[item] for item in (decisions.get(shot_id) or {}).get("selected_asset_ids", []) if item in assets]

    def unit_selected_assets(shot: dict[str, Any], unit: dict[str, Any]) -> list[dict[str, Any]]:
        ids = [str(value) for value in unit.get("delivery_asset_ids", [])]
        return [assets[value] for value in ids if value in assets]

    manifest_shots: list[dict[str, Any]] = []
    for shot in shots:
        shot_id = str(shot.get("id"))
        meta = pack_by_id.get(shot_id) or {}
        export_shot = export_shots.get(shot_id) or {}
        prompt_path = resolve(project_dir, meta.get("prompt_file") or f"prompts/{shot_id}.md")
        if prompt_path is None or not prompt_path.is_file():
            block("CANONICAL_PROMPT_MISSING", "Canonical prompt file is missing.", shot_id)
            prompt = ""
        else:
            prompt = prompt_from_markdown(prompt_path)
        prompt_hash = sha_text(prompt) if prompt else None
        prompt_count = len(re.sub(r"\s+", "", prompt)) if prompt else 0
        docx_shot = (parsed_docx or {}).get("shots", {}).get(shot_id, {})
        shot_docx_texts = docx_shot.get("texts", [])

        selected_assets = shot_selected_assets(shot_id)
        selected_asset_ids = [str(item.get("asset_id")) for item in selected_assets]
        canonical_shot_asset_ids = [
            str(asset_id)
            for _, unit in storyboard_units(shot)
            for asset_id in (unit.get("delivery_asset_ids") or [])
        ]
        export_selected_ids = [str(item) for item in (export_shot.get("selected_asset_ids") or [])]
        expected_shot_unit_ids = [unit_id(unit) for _, unit in storyboard_units(shot)]
        export_source_units = {str(item.get("source_shot_id")): item for item in (export_shot.get("source_units") or []) if isinstance(item, dict)}
        export_inserted_units = {str(item.get("inserted_shot_id")): item for item in (export_shot.get("inserted_units") or []) if isinstance(item, dict)}
        unit_results: list[dict[str, Any]] = []

        for kind, unit in storyboard_units(shot):
            item_id = unit_id(unit)
            card = (parsed_docx or {}).get("units", {}).get(item_id, {})
            card_texts = card.get("texts", [])
            selected = unit_selected_assets(shot, unit)
            expected_assets: list[dict[str, Any]] = []
            for asset in selected:
                asset_path = resolve(project_dir, asset.get("path"))
                responsibility = delivery_asset_responsibility(unit, asset)
                expected_assets.append(
                    {
                        "asset_id": str(asset.get("asset_id")),
                        "sha256": sha_file(asset_path) if asset_path and asset_path.is_file() else None,
                        "responsibility": responsibility,
                        "display_caption": target_frame_caption(item_id, asset, responsibility),
                    }
                )
            export_unit = (export_source_units if kind == "source" else export_inserted_units).get(item_id) or {}
            export_assets = export_unit.get("delivery_assets") or []
            expected_eating_occurrences = matching_eating_occurrences(story, shot_id, item_id)
            expected_eating_texts = [format_eating_occurrence(item) for item in expected_eating_occurrences]
            expected_break_occurrences = matching_break_occurrences(story, shot_id, item_id)
            expected_break_texts = [format_break_occurrence(item) for item in expected_break_occurrences]
            layer_text = format_performance_layers(unit)
            layer_complete = has_complete_performance_layers(unit)

            required_texts = [
                exact_label("准确秒数", exact_time_label(kind, unit)),
                exact_label("分镜描述", str(unit.get("storyboard_description"))),
                exact_label("口播稿", str(unit.get("script_text") or "无")),
            ]
            if kind == "inserted":
                required_texts.extend(
                    [
                        exact_label("新增原因", str(unit.get("insertion_rationale"))),
                        exact_label("节奏锚点", str(unit.get("rhythm_anchor"))),
                        exact_label("源片表演依据", "、".join(map(str, unit.get("source_reference_shot_ids") or []))),
                    ]
                )
            required_texts.extend(exact_label("吃食节奏证据", value) for value in expected_eating_texts)
            required_texts.extend(exact_label("掰开酥脆证据", value) for value in expected_break_texts)

            text_results = {value: value in card_texts for value in required_texts}
            source_time_matches = kind != "source" or export_unit.get("source_timecode") == (unit.get("source_timecode") or {})
            generation_time_matches = export_unit.get("generation_timecode") == (unit.get("generation_timecode") or {})
            description_matches = export_unit.get("storyboard_description") == unit.get("storyboard_description")
            script_matches = export_unit.get("script_text") == unit.get("script_text")
            export_asset_projection = [
                {
                    "asset_id": str(item.get("asset_id")),
                    "sha256": item.get("sha256"),
                    "responsibility": item.get("responsibility"),
                    "display_caption": item.get("display_caption"),
                }
                for item in export_assets
                if isinstance(item, dict)
            ]
            docx_frame_projection = [
                {
                    "asset_id": str(item.get("asset_id")),
                    "sha256": item.get("image_sha256"),
                    "responsibility": item.get("responsibility"),
                    "display_caption": item.get("caption"),
                }
                for item in card.get("frame_entries", [])
                if isinstance(item, dict)
            ]
            asset_manifest_matches = bool(expected_assets) and export_asset_projection == expected_assets
            image_hash_matches = bool(expected_assets) and docx_frame_projection == expected_assets
            card_shot_matches = card.get("shot_id") == shot_id
            unit_checks = {
                "body_card_present_under_correct_shot": card_shot_matches,
                "body_card_heading_matches": card.get("heading_text") == f"{item_id}｜{exact_time_label(kind, unit)}",
                "editable_labels_match": bool(required_texts) and all(text_results.values()),
                "source_timecode_matches_manifest": source_time_matches,
                "generation_timecode_matches_manifest": generation_time_matches,
                "storyboard_description_matches_manifest": description_matches,
                "script_matches_manifest": script_matches,
                "asset_id_and_hash_match_manifest": asset_manifest_matches,
                "target_frame_captions_and_hashes_match": image_hash_matches,
                "body_card_has_at_least_one_image_relationship": len(card.get("image_parts", [])) >= 1,
                "body_card_image_count_matches_owner_assets": len(card.get("image_parts", [])) == len(expected_assets),
                "performance_layers_match_manifest": export_unit.get("source_performance_layers") == unit.get("source_performance_layers")
                and export_unit.get("performance_layers_text") == layer_text
                and layer_complete
                and layer_text is not None
                and exact_label("六层证据", layer_text) not in card_texts,
                "eating_evidence_matches_manifest": export_unit.get("eating_occurrences") == expected_eating_occurrences
                and export_unit.get("eating_occurrence_texts") == expected_eating_texts,
                "break_evidence_matches_manifest": export_unit.get("break_occurrences") == expected_break_occurrences
                and export_unit.get("break_occurrence_texts") == expected_break_texts,
            }
            unit_status = "aligned" if all(unit_checks.values()) else "blocked"
            if unit_status == "blocked":
                failed = [key for key, value in unit_checks.items() if not value]
                block("UNIT_ALIGNMENT_FAILED", ", ".join(failed), shot_id, item_id)
            unit_results.append(
                {
                    "unit_id": item_id,
                    "kind": kind,
                    "expected_assets": expected_assets,
                    "docx_image_hashes": card.get("image_hashes", []),
                    "required_editable_text": text_results,
                    "checks": unit_checks,
                    "status": unit_status,
                }
            )

        if project_contract["enabled"]:
            character_count_aligned = (
                project_contract["minimum_non_whitespace_characters"]
                <= prompt_count
                <= project_contract["maximum_non_whitespace_characters"]
            )
        else:
            character_count_aligned = True
        prompt_file_hash = sha_file(prompt_path) if prompt_path and prompt_path.is_file() else None
        avatar_current = (shot.get("asset_links") or {}).get("avatar_reference")
        product_current = (shot.get("asset_links") or {}).get("product_references") or []
        package_faces = ((shot.get("product_state") or {}).get("package_artwork") or {}).get("visible_faces") or []
        package_face_texts = [format_package_face(face) for face in package_faces if isinstance(face, dict)]
        package_face_word_texts = [format_package_face_for_word(face) for face in package_faces if isinstance(face, dict)]
        package_docx_texts = {
            exact_label("包装盒面", value): exact_label("包装盒面", value) in shot_docx_texts
            for value in package_face_word_texts
        }
        expected_continuity: list[dict[str, Any]] = []
        for reference in shot.get("continuity_boundary_references") or []:
            owner_unit_id = str(reference.get("owner_unit_id"))
            asset_id = str(reference.get("asset_id"))
            asset = assets.get(asset_id) or {}
            asset_path = resolve(project_dir, asset.get("path"))
            responsibility = str(reference.get("responsibility") or "")
            expected_continuity.append(
                {
                    "owner_unit_id": owner_unit_id,
                    "asset_id": asset_id,
                    "sha256": sha_file(asset_path) if asset_path and asset_path.is_file() else None,
                    "responsibility": responsibility,
                    "continuity_boundary_reference": True,
                    "display_caption": target_frame_caption(owner_unit_id, asset, responsibility, continuity=True),
                }
            )
        export_continuity = export_shot.get("continuity_boundary_references") or []
        docx_continuity = [
            {
                "owner_unit_id": item.get("owner_unit_id"),
                "asset_id": item.get("asset_id"),
                "sha256": item.get("image_sha256"),
                "responsibility": item.get("responsibility"),
                "continuity_boundary_reference": item.get("continuity_boundary_reference"),
                "display_caption": item.get("caption"),
            }
            for item in docx_shot.get("continuity_boundary_references", [])
            if isinstance(item, dict)
        ]
        expected_segment_heading = (
            f"{shot_id}｜{' + '.join(expected_shot_unit_ids)}｜"
            f"{clock_label((shot.get('timecode') or {}).get('start'))}–"
            f"{clock_label((shot.get('timecode') or {}).get('end'))}"
        )
        shot_checks = {
            "segment_heading_and_total_time_aligned": docx_shot.get("heading_text") == expected_segment_heading,
            "prompt_text_aligned": bool(prompt) and prompt in shot_docx_texts,
            "prompt_hash_aligned": bool(prompt)
            and prompt_hash == meta.get("prompt_sha256") == export_shot.get("prompt_sha256"),
            "prompt_file_hash_aligned": bool(prompt_file_hash)
            and prompt_file_hash == meta.get("prompt_file_sha256") == export_shot.get("prompt_file_sha256"),
            "source_units_match_compile_pack": meta.get("source_units") == (shot.get("source_units") or []),
            "inserted_units_match_compile_pack": meta.get("inserted_units") == (shot.get("inserted_units") or []),
            "character_count_aligned": prompt_count == meta.get("prompt_non_whitespace_characters") == export_shot.get("prompt_non_whitespace_characters")
            and (export_manifest.get("prompt_non_whitespace_characters") or {}).get(shot_id) == prompt_count
            and character_count_aligned,
            "unit_order_aligned": docx_shot.get("unit_order") == expected_shot_unit_ids,
            "all_body_cards_aligned": bool(unit_results) and all(item["status"] == "aligned" for item in unit_results),
            "script_aligned": bool(unit_results)
            and all(item["checks"]["script_matches_manifest"] and item["checks"]["editable_labels_match"] for item in unit_results),
            "frame_aligned": bool(unit_results)
            and all(item["checks"]["target_frame_captions_and_hashes_match"] for item in unit_results),
            "selected_asset_ids_match_manifest": (
                selected_asset_ids == canonical_shot_asset_ids == export_selected_ids
            ),
            "continuity_boundary_references_aligned": (
                export_continuity == expected_continuity == docx_continuity
            ),
            "avatar_aligned": export_shot.get("avatar_reference") == avatar_current,
            "product_aligned": product_current
            == (meta.get("product_references") or [])
            == (export_shot.get("product_references") or []),
            "package_artwork_aligned": export_shot.get("package_faces", []) == package_faces
            and export_shot.get("package_face_texts", []) == package_face_texts
            and export_shot.get("package_face_word_texts", []) == package_face_word_texts
            and all(package_docx_texts.values()),
        }
        shot_status = "aligned" if all(shot_checks.values()) else "blocked"
        if shot_status == "blocked":
            block("SHOT_ALIGNMENT_FAILED", ", ".join(key for key, value in shot_checks.items() if not value), shot_id)
        manifest_shots.append(
            {
                "shot_id": shot_id,
                "title": shot.get("title"),
                "canonical_prompt": {
                    "path": str(prompt_path.relative_to(project_dir)) if prompt_path and prompt_path.is_relative_to(project_dir) else str(prompt_path),
                    "sha256": prompt_hash,
                    "non_whitespace_characters": prompt_count,
                },
                "selected_asset_ids": selected_asset_ids,
                "package_face_editable_text": package_docx_texts,
                "units": unit_results,
                "checks": shot_checks,
                "status": shot_status,
            }
        )

    aligned_count = sum(item["status"] == "aligned" for item in manifest_shots)
    status = "aligned" if aligned_count == len(shots) and not blockers and all(global_checks.values()) else "blocked"
    manifest = {
        "schema_version": "2.0",
        "project_id": project.get("project_id"),
        "compile_id": pack.get("compile_id"),
        "generated_at": now_iso(),
        "audit_mode": "read_only_canonical_and_docx",
        "docx_required": args.require_docx,
        "docx": str(docx_path) if docx_path else None,
        "export_manifest": str(export_manifest_path) if export_manifest_path else None,
        "global_checks": global_checks,
        "shots": manifest_shots,
        "summary": {
            "shot_count": len(shots),
            "unit_count": len(expected_unit_order),
            "aligned_count": aligned_count,
            "blocked_count": len(shots) - aligned_count,
            "status": status,
        },
        "blockers": blockers,
    }
    manifest_path = project_dir / "review" / "alignment_manifest.json"
    write_json(manifest_path, manifest)
    update_workflow(project_dir, status == "aligned", blockers)
    print(json.dumps({"alignment_manifest": str(manifest_path), "summary": manifest["summary"]}, ensure_ascii=False, indent=2))
    return 0 if status == "aligned" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, json.JSONDecodeError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(2)
