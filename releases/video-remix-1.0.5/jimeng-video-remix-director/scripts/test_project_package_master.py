#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw


SCRIPT = Path(__file__).with_name("project_package_master.py")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="package-master-projection-") as temporary:
        root = Path(temporary)
        candidate = Image.new("RGB", (320, 240), (90, 90, 90))
        ImageDraw.Draw(candidate).polygon([(50, 40), (280, 55), (260, 210), (65, 195)], fill=(180, 120, 70))
        candidate_path = root / "candidate.png"
        candidate.save(candidate_path)

        master = Image.new("RGB", (120, 120), (245, 120, 20))
        draw = ImageDraw.Draw(master)
        draw.rectangle((10, 10, 110, 35), fill=(20, 20, 20))
        draw.rectangle((10, 50, 45, 105), fill=(245, 220, 40))
        draw.rectangle((55, 50, 110, 105), fill=(220, 35, 35))
        master_path = root / "front-master.png"
        master.save(master_path)

        visible = Image.new("L", candidate.size, 255)
        ImageDraw.Draw(visible).rectangle((130, 70, 180, 200), fill=0)
        mask_path = root / "visible-mask.png"
        visible.save(mask_path)

        output_path = root / "projected.png"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--candidate",
                str(candidate_path),
                "--master",
                str(master_path),
                "--quad",
                "50,40,280,55,260,210,65,195",
                "--face",
                "front",
                "--visible-mask",
                str(mask_path),
                "--lighting-strength",
                "0",
                "--edge-feather",
                "0",
                "--output",
                str(output_path),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        output = Image.open(output_path).convert("RGB")
        # Outside the face remains the original gray candidate.
        assert output.getpixel((10, 10)) == (90, 90, 90)
        # A visible region receives deterministic master artwork.
        assert output.getpixel((90, 80)) != candidate.getpixel((90, 80))
        # The explicit black mask preserves candidate pixels under occlusion.
        assert output.getpixel((150, 120)) == candidate.getpixel((150, 120))

        manifest_path = output_path.with_suffix(".png.projection.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["schema_version"] == "package-master-projection-v1.0"
        assert manifest["projection_method"] == "homography"
        assert manifest["face"] == "front"
        assert manifest["model_redraw_used"] is False
        assert manifest["candidate"]["sha256"]
        assert manifest["master"]["sha256"]
        assert manifest["visible_mask"]["sha256"]
        assert manifest["output"]["sha256"]

    print("PACKAGE MASTER PROJECTION TEST PASSED")


if __name__ == "__main__":
    main()
