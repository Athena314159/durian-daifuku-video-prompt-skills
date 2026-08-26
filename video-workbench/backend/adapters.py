"""Adapters for the installed director scripts, ffmpeg and Codex CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


class Toolchain:
    def __init__(
        self,
        skill_dir: Optional[Path] = None,
        python_bin: Optional[str] = None,
        ffmpeg_bin: Optional[str] = None,
        ffprobe_bin: Optional[str] = None,
        codex_bin: Optional[str] = None,
    ) -> None:
        self.skill_dir = (skill_dir or Path.home() / ".codex" / "skills" / "jimeng-video-remix-director").resolve()
        self.python_bin = python_bin or sys.executable
        self.ffmpeg_bin = self._resolve_binary(ffmpeg_bin or "ffmpeg")
        self.ffprobe_bin = self._resolve_binary(ffprobe_bin or "ffprobe")
        self.codex_bin = self._resolve_binary(codex_bin or "codex")

    @staticmethod
    def _resolve_binary(value: str) -> Optional[str]:
        if os.path.isabs(value):
            return value if os.path.isfile(value) and os.access(value, os.X_OK) else None
        return shutil.which(value)

    def script(self, name: str) -> Optional[Path]:
        candidate = (self.skill_dir / "scripts" / name).resolve(strict=False)
        scripts_root = (self.skill_dir / "scripts").resolve(strict=False)
        if scripts_root not in candidate.parents or not candidate.is_file():
            return None
        return candidate

    def capabilities(self) -> Dict[str, Any]:
        return {
            "skill_dir": str(self.skill_dir),
            "skill_available": (self.skill_dir / "SKILL.md").is_file(),
            "ffmpeg": self.ffmpeg_bin,
            "ffprobe": self.ffprobe_bin,
            "codex": self.codex_bin,
            "codex_available": bool(self.codex_bin),
            "codex_default_enabled": False,
            "adapters": {
                "asr": "via_codex" if self.codex_bin else "unavailable",
                "image_generation": "unconfigured",
                "video_generation": "unconfigured",
                "jimeng_submission": "manual",
            },
        }

    def run_sync(self, command: List[str], cwd: Optional[Path] = None, timeout: int = 120) -> Dict[str, Any]:
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd) if cwd else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc), "command": command}
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "command": command,
        }

    def initialize_project_command(
        self,
        name: str,
        projects_root: Path,
        project_id: str,
        execution_tier: str,
        product_profile: Optional[str],
    ) -> Optional[List[str]]:
        script = self.script("init_project.py")
        if not script:
            return None
        command = [
            self.python_bin,
            str(script),
            "--name",
            name,
            "--output",
            str(projects_root),
            "--project-id",
            project_id,
            "--execution-tier",
            execution_tier,
            "--style-profile",
            "ugc-food-review-v1",
        ]
        if product_profile:
            command.extend(["--product-mode", "replace_product", "--product-profile", product_profile])
        return command

    def probe_video(self, video: Path) -> Dict[str, Any]:
        if not self.ffprobe_bin:
            return {"status": "blocked", "error_code": "FFPROBE_NOT_AVAILABLE"}
        result = self.run_sync(
            [
                self.ffprobe_bin,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_entries",
                "format=duration,format_name,bit_rate:stream=index,codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate",
                str(video),
            ],
            timeout=45,
        )
        if not result["ok"]:
            return {
                "status": "blocked",
                "error_code": "FFPROBE_FAILED",
                "error": (result.get("stderr") or "")[-3000:],
            }
        try:
            payload = json.loads(result["stdout"])
            streams = payload.get("streams") or []
            video_stream = next(stream for stream in streams if stream.get("codec_type") == "video")
            audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
            frame_rate = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0/0"
            if "/" in str(frame_rate):
                numerator, denominator = str(frame_rate).split("/", 1)
                fps = float(numerator) / float(denominator) if float(denominator) else None
            else:
                fps = float(frame_rate)
            duration_raw = (payload.get("format") or {}).get("duration")
            return {
                "status": "ready",
                "duration": float(duration_raw) if duration_raw not in (None, "N/A") else None,
                "width": int(video_stream.get("width")) if video_stream.get("width") is not None else None,
                "height": int(video_stream.get("height")) if video_stream.get("height") is not None else None,
                "fps": round(fps, 6) if fps is not None else None,
                "has_audio": audio_stream is not None,
                "video_codec": video_stream.get("codec_name"),
                "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
                "format": (payload.get("format") or {}).get("format_name"),
                "bit_rate": int((payload.get("format") or {}).get("bit_rate") or 0) or None,
            }
        except (ValueError, TypeError, StopIteration, json.JSONDecodeError) as exc:
            return {"status": "blocked", "error_code": "FFPROBE_INVALID_OUTPUT", "error": str(exc)}

    def probe_image(self, image: Path) -> Dict[str, Any]:
        """Read still-image geometry from the file itself, never its suffix."""
        if not self.ffprobe_bin:
            return {"status": "blocked", "error_code": "FFPROBE_NOT_AVAILABLE"}
        result = self.run_sync(
            [
                self.ffprobe_bin,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_entries",
                "format=format_name:stream=index,codec_type,codec_name,width,height",
                "-select_streams",
                "v:0",
                str(image),
            ],
            timeout=45,
        )
        if not result["ok"]:
            return {
                "status": "blocked",
                "error_code": "FFPROBE_FAILED",
                "error": (result.get("stderr") or "")[-3000:],
            }
        try:
            payload = json.loads(result["stdout"])
            streams = payload.get("streams") or []
            image_stream = next(stream for stream in streams if stream.get("codec_type") == "video")
            width = int(image_stream.get("width"))
            height = int(image_stream.get("height"))
            if width <= 0 or height <= 0:
                raise ValueError("image width and height must be positive")
            return {
                "status": "ready",
                "width": width,
                "height": height,
                "format": (payload.get("format") or {}).get("format_name"),
                "codec": image_stream.get("codec_name"),
            }
        except (ValueError, TypeError, StopIteration, json.JSONDecodeError) as exc:
            return {"status": "blocked", "error_code": "FFPROBE_INVALID_OUTPUT", "error": str(exc)}

    def verify_decode(self, media: Path, kind: str) -> Dict[str, Any]:
        """Ask ffmpeg to actually decode the selected visual stream.

        Video results are decoded through the complete visual stream. This costs
        more than trusting container metadata, but prevents renamed/corrupt files
        from entering an approval manifest as real Jimeng outputs.
        """
        if not self.ffmpeg_bin:
            return {"status": "blocked", "error_code": "FFMPEG_NOT_AVAILABLE"}
        command = [
            self.ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-i",
            str(media),
            "-map",
            "0:v:0",
        ]
        if kind == "image":
            command.extend(["-frames:v", "1"])
        command.extend(["-f", "null", "-"])
        result = self.run_sync(command, timeout=600 if kind == "video" else 90)
        if not result["ok"]:
            return {
                "status": "blocked",
                "error_code": "FFMPEG_DECODE_FAILED",
                "error": (result.get("stderr") or "")[-3000:],
            }
        return {"status": "ready"}

    def inspect_image(self, image: Path) -> Dict[str, Any]:
        metadata = self.probe_image(image)
        if metadata.get("status") != "ready":
            return metadata
        decode = self.verify_decode(image, "image")
        if decode.get("status") != "ready":
            return decode
        metadata["decode_status"] = "ready"
        return metadata

    def inspect_video(self, video: Path) -> Dict[str, Any]:
        metadata = self.probe_video(video)
        if metadata.get("status") != "ready":
            return metadata
        required = (metadata.get("duration"), metadata.get("width"), metadata.get("height"), metadata.get("fps"))
        if (
            any(value is None for value in required)
            or float(metadata["duration"]) <= 0
            or int(metadata["width"]) <= 0
            or int(metadata["height"]) <= 0
            or float(metadata["fps"]) <= 0
        ):
            return {
                "status": "blocked",
                "error_code": "INCOMPLETE_VIDEO_METADATA",
                "error": "duration, width, height and fps must be positive",
            }
        decode = self.verify_decode(video, "video")
        if decode.get("status") != "ready":
            return decode
        metadata["decode_status"] = "ready"
        return metadata

    def make_thumbnail(self, video: Path, destination: Path, duration: Optional[float]) -> Dict[str, Any]:
        if not self.ffmpeg_bin:
            return {"status": "blocked", "error_code": "FFMPEG_NOT_AVAILABLE"}
        seek = min(3.0, max(0.0, (duration or 0.0) * 0.1))
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = self.run_sync(
            [
                self.ffmpeg_bin,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                "%.3f" % seek,
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-y",
                str(destination),
            ],
            timeout=90,
        )
        if not result["ok"] or not destination.is_file():
            if destination.exists():
                destination.unlink()
            return {
                "status": "blocked",
                "error_code": "THUMBNAIL_FAILED",
                "error": (result.get("stderr") or "")[-3000:],
            }
        return {"status": "ready", "path": str(destination)}

    def director_command(self, operation: str, project_dir: Path, video: Optional[Path] = None) -> Optional[List[str]]:
        if operation == "analyze":
            script = self.script("extract_video_assets.py")
            if not script or video is None:
                return None
            return [
                self.python_bin,
                str(script),
                "--video",
                str(video),
                "--project-dir",
                str(project_dir),
                "--interval",
                "1.0",
                "--scene-threshold",
                "0.28",
            ]
        if operation == "extract_frames":
            script = self.script("extract_shot_frames.py")
            return [self.python_bin, str(script), "--project-dir", str(project_dir), "--candidates", "5"] if script else None
        if operation in {"lint", "compile", "verify"}:
            script = self.script("pipeline.py")
            if not script:
                return None
            subcommand = "verify-prompt-delivery" if operation == "verify" else operation
            return [self.python_bin, str(script), subcommand, "--project-dir", str(project_dir)]
        if operation == "export_docx":
            script = self.script("export_jimeng_docx.py")
            if not script:
                return None
            output = project_dir / "exports" / (project_dir.name + "_即梦逐分镜执行稿.docx")
            manifest = project_dir / "review" / (project_dir.name + "_即梦逐分镜执行稿.manifest.json")
            return [
                self.python_bin,
                str(script),
                "--project-dir",
                str(project_dir),
                "--out",
                str(output),
                "--manifest-out",
                str(manifest),
            ]
        if operation == "align":
            script = self.script("align_exports.py")
            if not script:
                return None
            output = project_dir / "exports" / (project_dir.name + "_即梦逐分镜执行稿.docx")
            return [self.python_bin, str(script), "--project-dir", str(project_dir), "--docx", str(output), "--require-docx"]
        return None

    def codex_command(
        self,
        project_dir: Path,
        schema_path: Path,
        result_path: Path,
        model: Optional[str] = None,
        read_only: bool = False,
    ) -> Optional[List[str]]:
        if not self.codex_bin:
            return None
        command = [
            self.codex_bin,
            "exec",
            "--json",
            "--ephemeral",
            "--sandbox",
            "read-only" if read_only else "workspace-write",
            "--skip-git-repo-check",
            "-C",
            str(project_dir),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(result_path),
        ]
        if model:
            command.extend(["--model", model])
        command.append("-")
        return command
