#!/usr/bin/env python3
"""Small regression tests for the V3 Canonical DOCX export gate."""

import base64
import hashlib
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import export_docx_from_build as exporter


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_build(root: Path, image_status: str = "user_approved", mutate_prompt: bool = False) -> Path:
    build = root / "build"
    build.mkdir(parents=True)
    prompt = root / "S001.md"
    prompt.write_text("镜头一：人物抬手展示榴莲大福。", encoding="utf-8")
    prompt_hash = digest(prompt)
    image = root / "S001.png"
    image.write_bytes(PNG_1X1)
    image_hash = digest(image)
    script = {"lines": [{"line_id": "L001", "text": "现在试一下。", "order": 1, "shot_id": "S001", "speaker_id": "Person-01"}]}
    final = {"project_id": "docx-test", "selected_shots": ["S001"]}
    prompts = {"tasks": [{"shot_id": "S001", "line_ids": ["L001"], "prompt_file": str(prompt), "prompt_file_sha256": prompt_hash}]}
    images = {"tasks": [{"shot_id": "S001", "image_status": image_status, "approved_image_path": str(image), "approved_image_sha256": image_hash}]}
    receipt = {"project_id": "docx-test", "prompt_file_sha256": {"S001": prompt_hash}}
    for name, value in (("final_generation_manifest.json", final), ("script_shot_map.json", script), ("prompt_task_manifest.json", prompts), ("image_task_manifest.json", images), ("rule_receipt.json", receipt)):
        (build / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    if mutate_prompt:
        prompt.write_text("已被改写的旧Prompt。", encoding="utf-8")
    return build


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        blocked = write_build(root / "awaiting", image_status="awaiting_generation")
        try:
            exporter.validate_build(blocked)
        except exporter.ExportBlocked as exc:
            assert "awaiting_generation" in str(exc)
        else:
            raise AssertionError("awaiting_generation must block export")

        stale = write_build(root / "stale", mutate_prompt=True)
        try:
            exporter.validate_build(stale)
        except exporter.ExportBlocked as exc:
            assert "Prompt哈希不一致" in str(exc)
        else:
            raise AssertionError("stale Prompt must block export")

        bad_image = write_build(root / "bad_image")
        image_manifest = bad_image / "image_task_manifest.json"
        image_data = json.loads(image_manifest.read_text(encoding="utf-8"))
        image_data["tasks"][0]["approved_image_sha256"] = "0" * 64
        image_manifest.write_text(json.dumps(image_data), encoding="utf-8")
        try:
            exporter.validate_build(bad_image)
        except exporter.ExportBlocked as exc:
            assert "QA图哈希不一致" in str(exc)
        else:
            raise AssertionError("QA image hash mismatch must block export")

        good = write_build(root / "good")
        output = root / "delivery.docx"
        alignment = root / "alignment_manifest.json"
        exporter.export_docx(good, output, alignment)
        assert output.is_file() and output.stat().st_size > 0
        report = json.loads(alignment.read_text(encoding="utf-8"))
        assert report["selected_shots"] == ["S001"]
        assert report["shots"][0]["line_ids"] == ["L001"]
        assert report["shots"][0]["image_sha256"] == digest(root / "good" / "S001.png")
        from docx import Document
        paragraphs = [paragraph.text for paragraph in Document(output).paragraphs]
        assert any("现在试一下" in text for text in paragraphs)
        assert any("镜头一" in text for text in paragraphs)
    print("DOCX EXPORT TEST PASSED")


if __name__ == "__main__":
    main()
