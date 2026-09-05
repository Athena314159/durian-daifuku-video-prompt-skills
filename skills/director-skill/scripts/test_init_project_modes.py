#!/usr/bin/env python3
"""Regression tests for preserve-source versus explicit replacement initialization."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from init_project import initialize_project


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="init-product-mode-") as temporary:
        root = Path(temporary)
        preserved = initialize_project(
            name="原片 intake",
            output=root,
            product_profile=None,
            style_profile="ugc-food-review-v1",
            project_id="preserved",
        )
        preserved_project = read(preserved / "project.json")
        assert preserved_project["product_mode"] == "preserve_source_product"
        assert preserved_project["execution_tier"] == "source_intake"
        assert read(preserved / "planning" / "workflow_state.json")["execution_tier"] == "source_intake"
        assert preserved_project["product_profile"] is None
        assert not (preserved / "library" / "product_bible.json").exists()
        assert read(preserved / "library" / "product_library.json")["products"] == []

        replaced = initialize_project(
            name="换品项目",
            output=root,
            product_profile="butter-crisp-v1",
            style_profile="ugc-food-review-v1",
            project_id="replaced",
        )
        replaced_project = read(replaced / "project.json")
        assert replaced_project["product_mode"] == "replace_product"
        assert replaced_project["product_profile"] == "butter-crisp-v1"
        assert (replaced / "library" / "product_bible.json").is_file()

    print("PROJECT PRODUCT MODE TESTS PASSED: 2 cases")


if __name__ == "__main__":
    main()
