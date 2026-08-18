#!/usr/bin/env python3
"""Extract non-destructive analysis assets from a source video using ffmpeg."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


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


def run(command: List[str], allow_failure: bool = False) -> subprocess.CompletedProcess:
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if completed.returncode != 0 and not allow_failure:
        detail = completed.stderr.strip()[-3000:]
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command[:4])}\n{detail}")
    return completed


def relative_or_absolute(path: Path, project_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_dir.resolve()))
    except ValueError:
        return str(path.resolve())


def parse_frame_rate(value: Optional[str]) -> Optional[float]:
    if not value or value in {"0/0", "N/A"}:
        return None
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        denominator_value = float(denominator)
        return float(numerator) / denominator_value if denominator_value else None
    return float(value)


def parse_showinfo_times(stderr: str) -> List[float]:
    return [float(value) for value in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", stderr)]


def probe_video(ffprobe: str, video: Path) -> Dict[str, Any]:
    completed = run(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_entries",
            "format=duration:stream=index,codec_type,width,height,avg_frame_rate,r_frame_rate",
            str(video),
        ]
    )
    data = json.loads(completed.stdout)
    video_stream = next(
        (stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"),
        None,
    )
    if not video_stream:
        raise RuntimeError("No video stream found.")
    audio_stream = next(
        (stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"),
        None,
    )
    duration = data.get("format", {}).get("duration")
    frame_rate_value = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
    return {
        "duration": float(duration) if duration not in (None, "N/A") else None,
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "frame_rate": parse_frame_rate(frame_rate_value),
        "has_audio": audio_stream is not None,
    }


def extract_assets(
    video: Path,
    project_dir: Path,
    interval: float,
    scene_threshold: float,
    copy_source: bool,
) -> Dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe are required. Install them or use an environment that provides them.")

    video = video.expanduser().resolve()
    project_dir = project_dir.expanduser().resolve()
    if not video.is_file():
        raise FileNotFoundError(f"Video not found: {video}")
    if not (project_dir / "project.json").is_file():
        raise FileNotFoundError(f"Not a project directory: {project_dir}")
    if interval <= 0:
        raise ValueError("--interval must be greater than 0.")
    if not 0 < scene_threshold < 1:
        raise ValueError("--scene-threshold must be between 0 and 1.")

    run_id = datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    analysis_dir = project_dir / "source" / "analysis" / run_id
    interval_dir = analysis_dir / "interval_frames"
    scene_dir = analysis_dir / "scene_frames"
    audio_dir = analysis_dir / "audio"
    for directory in (interval_dir, scene_dir, audio_dir):
        directory.mkdir(parents=True, exist_ok=False)

    source_path = video
    if copy_source:
        copied_path = project_dir / "source" / f"original{video.suffix.lower()}"
        if copied_path.exists():
            raise FileExistsError(f"Copied source already exists: {copied_path}")
        shutil.copy2(video, copied_path)
        source_path = copied_path

    metadata = probe_video(ffprobe, source_path)
    source_hash = sha256_file(source_path)

    video_first_frame = analysis_dir / "video_first_frame.jpg"
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "0",
            "-i",
            str(source_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            str(video_first_frame),
        ]
    )

    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source_path),
            "-vf",
            f"fps=1/{interval}",
            "-q:v",
            "3",
            "-y",
            str(interval_dir / "interval_%05d.jpg"),
        ]
    )

    scene_completed = run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            str(source_path),
            "-vf",
            f"select=gt(scene\\,{scene_threshold}),showinfo",
            "-fps_mode",
            "vfr",
            "-q:v",
            "2",
            "-y",
            str(scene_dir / "scene_%05d.jpg"),
        ],
        allow_failure=True,
    )

    audio_path = audio_dir / "source_audio.wav"
    audio_extracted = False
    audio_error = None
    if metadata.get("has_audio"):
        audio_completed = run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-y",
                str(audio_path),
            ],
            allow_failure=True,
        )
        audio_extracted = audio_completed.returncode == 0 and audio_path.is_file()
        if not audio_extracted:
            audio_error = audio_completed.stderr.strip()[-1000:]

    interval_frames = sorted(interval_dir.glob("*.jpg"))
    scene_frames = sorted(scene_dir.glob("*.jpg"))
    scene_times = parse_showinfo_times(scene_completed.stderr)
    interval_candidates = [
        {"frame": relative_or_absolute(path, project_dir), "time": round(index * interval, 3)}
        for index, path in enumerate(interval_frames)
    ]
    scene_candidates = [
        {
            "frame": relative_or_absolute(path, project_dir),
            "time": round(scene_times[index], 3) if index < len(scene_times) else None,
        }
        for index, path in enumerate(scene_frames)
    ]

    source_manifest_path = project_dir / "source" / "source_manifest.json"
    manifest = load_json(source_manifest_path)
    analysis_record = {
        "run_id": run_id,
        "interval_seconds": interval,
        "scene_threshold": scene_threshold,
        "video_first_frame": relative_or_absolute(video_first_frame, project_dir),
        "interval_frames": [relative_or_absolute(path, project_dir) for path in interval_frames],
        "scene_frames": [relative_or_absolute(path, project_dir) for path in scene_frames],
        "interval_candidates": interval_candidates,
        "scene_candidates": scene_candidates,
        "audio": relative_or_absolute(audio_path, project_dir) if audio_extracted else None,
        "scene_extraction_error": scene_completed.stderr.strip()[-1000:] if scene_completed.returncode else None,
        "audio_error": audio_error,
        "created_at": now_iso(),
    }
    manifest.update(
        {
            "source_video": relative_or_absolute(source_path, project_dir),
            "sha256": source_hash,
            "duration": metadata.get("duration"),
            "width": metadata.get("width"),
            "height": metadata.get("height"),
            "frame_rate": metadata.get("frame_rate"),
            "audio_extracted": audio_extracted,
            "video_first_frame": analysis_record["video_first_frame"],
            "interval_frames": analysis_record["interval_frames"],
            "scene_frames": analysis_record["scene_frames"],
            "interval_candidates": interval_candidates,
            "scene_candidates": scene_candidates,
            "audio": analysis_record["audio"],
            "analyzed_at": now_iso(),
            "current_analysis": run_id,
        }
    )
    manifest.setdefault("analysis_runs", []).append(analysis_record)
    write_json(source_manifest_path, manifest)

    project_path = project_dir / "project.json"
    project = load_json(project_path)
    project["source_video"] = relative_or_absolute(source_path, project_dir)
    project["updated_at"] = now_iso()
    if project.get("status") == "draft":
        project["status"] = "analyzed"
    write_json(project_path, project)

    return {
        "project_dir": str(project_dir),
        "analysis_dir": relative_or_absolute(analysis_dir, project_dir),
        "metadata": metadata,
        "sha256": source_hash,
        "interval_frame_count": len(interval_frames),
        "scene_frame_count": len(scene_frames),
        "audio_extracted": audio_extracted,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract first frame, candidate frames and audio without changing the source video.")
    parser.add_argument("--video", required=True, type=Path, help="Source video path.")
    parser.add_argument("--project-dir", required=True, type=Path, help="Initialized project directory.")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between interval frames.")
    parser.add_argument("--scene-threshold", type=float, default=0.28, help="ffmpeg scene-change threshold (0-1).")
    parser.add_argument("--copy-source", action="store_true", help="Copy the source video into the project instead of referencing it.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = extract_assets(
            video=args.video,
            project_dir=args.project_dir,
            interval=args.interval,
            scene_threshold=args.scene_threshold,
            copy_source=args.copy_source,
        )
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
