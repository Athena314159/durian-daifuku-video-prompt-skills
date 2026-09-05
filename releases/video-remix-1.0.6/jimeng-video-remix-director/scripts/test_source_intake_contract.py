#!/usr/bin/env python3
"""Regression tests for source-intake handoff and direct transcript rendering."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VALIDATOR = ROOT / "validate_source_intake_handoff.py"
RENDERER = ROOT / "render_source_transcript.py"
GALLERY_RENDERER = ROOT / "render_source_gallery.py"


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(script: Path, path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(script), "--handoff", str(path)], capture_output=True, text=True, check=False)


def text_handoff() -> dict:
    return {
        "schema_version": "source-intake-handoff-v1.0",
        "execution_tier": "source_intake",
        "branch_role": "text",
        "source_intake_contract_sha256": "a" * 64,
        "product_mode": "preserve_source_product",
        "target_product_reference_bound": False,
        "status": "transcript_ready",
        "pending_inputs": ["revised_script"],
        "blocked_items": [],
        "artifacts": ["source_transcript.json"],
        "language_detection": {
            "decision_source": "visible_subtitles",
            "evidence_priority": ["visible_subtitles", "automatic_language_detection", "speech_audio", "lip_reading"],
            "evidence_used": ["visible_subtitles", "automatic_language_detection"],
            "excluded_signals": ["product_name", "brand_name", "country_name", "origin_label"],
        },
        "transcript": {
            "source_language": "zh-CN",
            "editable_text": "今天给大家试一下这个产品。[待核 00:03.20–00:03.80] 口感很酥脆。",
            "segments": [
                {"start": 0.0, "end": 3.8, "text": "今天给大家试一下这个产品。[待核]", "evidence": ["visible_subtitles"], "confidence": 0.88},
                {"start": 3.8, "end": 5.2, "text": "口感很酥脆。", "evidence": ["speech_audio"], "confidence": 0.81},
            ],
        },
        "controller_reply": {
            "must_inline_editable_text": True,
            "may_only_report_path": False,
            "deliver_before_other_branch_complete": True,
        },
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="source-intake-") as temporary:
        path = Path(temporary) / "handoff.json"
        valid = text_handoff()
        write(path, valid)
        checked = run(VALIDATOR, path)
        assert checked.returncode == 0, checked.stdout + checked.stderr

        rendered = run(RENDERER, path)
        assert rendered.returncode == 0, rendered.stdout + rendered.stderr
        assert "原片口播（可直接修改）" in rendered.stdout
        assert valid["transcript"]["editable_text"] in rendered.stdout
        assert "handoff.json" not in rendered.stdout

        wrong_language_signal = copy.deepcopy(valid)
        wrong_language_signal["language_detection"]["evidence_used"] = ["country_name"]
        write(path, wrong_language_signal)
        rejected_language = run(VALIDATOR, path)
        assert rejected_language.returncode == 2
        assert "unsupported signal" in rejected_language.stdout

        preserve_but_waiting_product = copy.deepcopy(valid)
        preserve_but_waiting_product["pending_inputs"].append("target_product_reference")
        write(path, preserve_but_waiting_product)
        rejected_product = run(VALIDATOR, path)
        assert rejected_product.returncode == 2
        assert "must not await" in rejected_product.stdout

        path_only = copy.deepcopy(valid)
        path_only["controller_reply"]["may_only_report_path"] = True
        write(path, path_only)
        rejected_path_only = run(VALIDATOR, path)
        assert rejected_path_only.returncode == 2
        assert "may not report only" in rejected_path_only.stdout

        replacement_wait = copy.deepcopy(valid)
        replacement_wait["product_mode"] = "replace_product"
        replacement_wait["pending_inputs"] = ["revised_script", "target_product_reference"]
        write(path, replacement_wait)
        valid_replacement_wait = run(VALIDATOR, path)
        assert valid_replacement_wait.returncode == 0, valid_replacement_wait.stdout

        first_frame = Path(temporary) / "SRC01.jpg"
        second_frame = Path(temporary) / "SRC02.jpg"
        first_frame.write_bytes(b"source frame 1")
        second_frame.write_bytes(b"source frame 2")
        image = {
            "schema_version": "source-intake-handoff-v1.0",
            "execution_tier": "source_intake",
            "branch_role": "image",
            "source_intake_contract_sha256": "b" * 64,
            "product_mode": "preserve_source_product",
            "target_product_reference_bound": False,
            "status": "source_inventory_ready",
            "pending_inputs": [],
            "blocked_items": [],
            "artifacts": ["source_inventory.json"],
            "source_inventory": {
                "source_shot_ids": ["SRC01", "SRC02"],
                # Deliberately unordered: the renderer, not handoff insertion order,
                # owns deterministic SRC ordering.
                "source_shots": [
                    {
                        "source_shot_id": "SRC02",
                        "timecode": {"start": 1.25, "end": 2.5, "duration": 1.25},
                        "image_path": str(second_frame),
                        "caption": "人物举起包装",
                    },
                    {
                        "source_shot_id": "SRC01",
                        "timecode": {"start": 0.0, "end": 1.25, "duration": 1.25},
                        "image_path": str(first_frame),
                        "caption": "人物手持产品近景",
                    },
                ],
            },
            "controller_reply": {
                "must_inline_images": True,
                "may_only_report_path": False,
                "deliver_when_ready": True,
            },
        }
        write(path, image)
        valid_image = run(VALIDATOR, path)
        assert valid_image.returncode == 0, valid_image.stdout

        gallery = run(GALLERY_RENDERER, path)
        assert gallery.returncode == 0, gallery.stdout + gallery.stderr
        first_markdown = f"![SRC01｜00:00.000–00:01.250｜人物手持产品近景]({first_frame})"
        second_markdown = f"![SRC02｜00:01.250–00:02.500｜人物举起包装]({second_frame})"
        assert first_markdown in gallery.stdout
        assert second_markdown in gallery.stdout
        assert gallery.stdout.index(first_markdown) < gallery.stdout.index(second_markdown)
        assert "image_handoff.json" not in gallery.stdout

        missing_image = copy.deepcopy(image)
        del missing_image["source_inventory"]["source_shots"][0]["image_path"]
        write(path, missing_image)
        rejected_missing_image = run(VALIDATOR, path)
        assert rejected_missing_image.returncode == 2
        assert "image_path missing" in rejected_missing_image.stdout

        relative_image = copy.deepcopy(image)
        relative_image["source_inventory"]["source_shots"][0]["image_path"] = "frames/SRC02.jpg"
        write(path, relative_image)
        rejected_relative_image = run(VALIDATOR, path)
        assert rejected_relative_image.returncode == 2
        assert "must be an absolute path" in rejected_relative_image.stdout

        missing_src = copy.deepcopy(image)
        missing_src["source_inventory"]["source_shots"].pop(0)
        write(path, missing_src)
        rejected_missing_src = run(VALIDATOR, path)
        assert rejected_missing_src.returncode == 2
        assert "cover every declared SRC exactly once" in rejected_missing_src.stdout

        image_path_only = copy.deepcopy(image)
        image_path_only["controller_reply"]["may_only_report_path"] = True
        write(path, image_path_only)
        rejected_image_path_only = run(VALIDATOR, path)
        assert rejected_image_path_only.returncode == 2
        assert "may not report only an image handoff path" in rejected_image_path_only.stdout

        nonexistent_image = copy.deepcopy(image)
        nonexistent_image["source_inventory"]["source_shots"][0]["image_path"] = str(Path(temporary) / "SRC02-missing.jpg")
        write(path, nonexistent_image)
        rejected_nonexistent_image = run(VALIDATOR, path)
        assert rejected_nonexistent_image.returncode == 2
        assert "image_path does not exist" in rejected_nonexistent_image.stdout

        fake_blocked = copy.deepcopy(image)
        fake_blocked["status"] = "blocked"
        write(path, fake_blocked)
        rejected_fake_block = run(VALIDATOR, path)
        assert rejected_fake_block.returncode == 2
        assert "requires observable blocked_items" in rejected_fake_block.stdout

    print("SOURCE INTAKE CONTRACT TESTS PASSED: 14 cases")


if __name__ == "__main__":
    main()
