#!/usr/bin/env python3
"""Extract the exact temporal first frame and separate beauty candidates for every shot."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def run(command: List[str]) -> None:
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip()[-3000:])


def relative(path: Path, project_dir: Path) -> str:
    return str(path.resolve().relative_to(project_dir.resolve()))


def extract_frame(ffmpeg: str, video: Path, timestamp: float, output: Path) -> None:
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            str(output),
        ]
    )


def resolve_source(project_dir: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else project_dir / candidate


def extract_shot_frames(project_dir: Path, candidates: int) -> Dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required.")
    if candidates < 3 or candidates > 9:
        raise ValueError("--candidates must be between 3 and 9.")

    project_dir = project_dir.expanduser().resolve()
    project = load_json(project_dir / "project.json")
    manifest_path = project_dir / "shots" / "shot_manifest.json"
    manifest = load_json(manifest_path)
    source_value = project.get("source_video")
    if not source_value:
        raise ValueError("project.json.source_video is empty.")
    video = resolve_source(project_dir, source_value).resolve()
    if not video.is_file():
        raise FileNotFoundError(f"Source video not found: {video}")

    extracted = []
    for shot in manifest.get("shots", []):
        shot_id = str(shot.get("id") or "").strip()
        timecode = shot.get("timecode") or {}
        start = timecode.get("start")
        end = timecode.get("end")
        if not shot_id or not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
            raise ValueError(f"Invalid shot id/timecode: {shot_id or '<missing>'}")

        units = shot.get("source_units") or []
        unit_results = []
        if units:
            for unit in units:
                source_id = str(unit.get("source_shot_id") or "").strip()
                source_timecode = unit.get("source_timecode") or {}
                unit_start, unit_end = source_timecode.get("start"), source_timecode.get("end")
                if not source_id or not isinstance(unit_start, (int, float)) or not isinstance(unit_end, (int, float)) or unit_end <= unit_start:
                    raise ValueError(f"Invalid source unit id/timecode in {shot_id}: {source_id or '<missing>'}")
                unit_dir = project_dir / "source" / "shot_frames" / source_id
                unit_dir.mkdir(parents=True, exist_ok=True)
                unit_first = unit_dir / "source_first_frame.jpg"
                extract_frame(ffmpeg, video, float(unit_start), unit_first)
                unit_candidates = []
                unit_duration = float(unit_end) - float(unit_start)
                for index in range(candidates):
                    fraction = (index + 1) / (candidates + 1)
                    timestamp = float(unit_start) + unit_duration * fraction
                    output = unit_dir / f"beauty_candidate_{index + 1:02d}.jpg"
                    extract_frame(ffmpeg, video, timestamp, output)
                    unit_candidates.append(relative(output, project_dir))
                unit["source_first_frame"] = relative(unit_first, project_dir)
                unit["beauty_keyframe_candidates"] = unit_candidates
                unit.setdefault("selected_beauty_keyframe", None)
                unit.setdefault("delivery_asset_ids", [])
                unit_results.append(
                    {
                        "source_shot_id": source_id,
                        "source_first_frame": unit["source_first_frame"],
                        "beauty_keyframe_candidates": unit_candidates,
                    }
                )
            first_path = resolve_source(project_dir, units[0]["source_first_frame"])
            candidate_paths = [
                value
                for unit in units
                for value in unit.get("beauty_keyframe_candidates", [])
            ]
        elif shot.get("inserted_units"):
            # Added storyboard units have no exact temporal source first frame.
            # Their required source_reference_frame is bound by planning/image QA;
            # never seek the source video using generation-timeline seconds.
            first_path = None
            candidate_paths = []
        else:
            shot_dir = project_dir / "source" / "shot_frames" / shot_id
            shot_dir.mkdir(parents=True, exist_ok=True)
            first_path = shot_dir / "source_first_frame.jpg"
            extract_frame(ffmpeg, video, float(start), first_path)
            duration = float(end) - float(start)
            candidate_paths = []
            for index in range(candidates):
                fraction = (index + 1) / (candidates + 1)
                timestamp = float(start) + duration * fraction
                output = shot_dir / f"beauty_candidate_{index + 1:02d}.jpg"
                extract_frame(ffmpeg, video, timestamp, output)
                candidate_paths.append(relative(output, project_dir))

        assets = shot.setdefault("asset_links", {})
        assets["source_first_frame"] = relative(first_path, project_dir) if first_path else None
        assets["beauty_keyframe_candidates"] = candidate_paths
        assets.setdefault("selected_beauty_keyframe", None)
        assets.setdefault("approved_generation_first_frame", None)
        extracted.append(
            {
                "shot_id": shot_id,
                "source_first_frame": assets["source_first_frame"],
                "beauty_keyframe_candidates": candidate_paths,
                "source_units": unit_results,
            }
        )

    write_json(manifest_path, manifest)
    return {"project_dir": str(project_dir), "shot_count": len(extracted), "shots": extracted}


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract per-shot first frames and separate beauty candidates.")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--candidates", type=int, default=5)
    args = parser.parse_args()
    try:
        result = extract_shot_frames(args.project_dir, args.candidates)
    except (FileNotFoundError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
