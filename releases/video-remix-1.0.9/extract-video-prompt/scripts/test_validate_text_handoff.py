#!/usr/bin/env python3
"""Cross-validator regressions for canonical text-handoff-v2.0."""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

from validate_text_handoff import compute_shot_map_sha256, validate_text_handoff


SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_ROOT = SCRIPT_DIR.parents[1]
DIRECTOR_SCRIPTS = SKILLS_ROOT / "jimeng-video-remix-director" / "scripts"
DIRECTOR_VALIDATOR = DIRECTOR_SCRIPTS / "validate_branch_handoff.py"
DIRECTOR_TEST = DIRECTOR_SCRIPTS / "test_validate_branch_handoff.py"
EXTRACT_VALIDATOR = SCRIPT_DIR / "validate_text_handoff.py"
EXTRACT_SCHEMA = SCRIPT_DIR.parent / "references" / "schemas" / "text_handoff.schema.json"
DIRECTOR_SCHEMA = SKILLS_ROOT / "jimeng-video-remix-director" / "references" / "schemas" / "text_handoff.schema.json"


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if str(DIRECTOR_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(DIRECTOR_SCRIPTS))
DIRECTOR = load_module("_director_validator_for_extract_test", DIRECTOR_VALIDATOR)
DIRECTOR_FIXTURE = load_module("_director_handoff_fixture_for_extract_test", DIRECTOR_TEST)


def write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def relock(text_handoff: dict[str, Any], locked: dict[str, Any]) -> None:
    for field in DIRECTOR.SHOT_MAP_HASH_FIELDS:
        text_handoff[field] = copy.deepcopy(locked[field])
    digest = DIRECTOR.semantic_shot_map_sha256(locked)
    text_handoff["locked_semantic_hash"] = digest
    text_handoff["shot_map_sha256"] = digest


class TextHandoffV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="extract-text-v2-")
        self.root = Path(self.temporary.name)
        (
            self.locked_path,
            self.text_path,
            self.image_path,
            self.locked,
            self.text_handoff,
            self.image_handoff,
        ) = DIRECTOR_FIXTURE.build_contract(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def director_errors(self, handoff: dict[str, Any], locked: dict[str, Any]) -> list[str]:
        errors, _contract = DIRECTOR.validate_handoff(handoff, locked)
        return errors

    def assert_rejected_by_both(self, handoff: dict[str, Any], locked: dict[str, Any]) -> None:
        extract_issues = validate_text_handoff(handoff, locked_shot_map=locked)
        director_issues = self.director_errors(handoff, locked)
        self.assertTrue(extract_issues, "extract validator unexpectedly accepted malformed v2")
        self.assertTrue(director_issues, "director validator unexpectedly accepted malformed v2")

    def test_schema_is_byte_for_byte_canonical_director_v2(self) -> None:
        self.assertEqual(DIRECTOR_SCHEMA.read_bytes(), EXTRACT_SCHEMA.read_bytes())

    def test_positive_payload_passes_both_python_apis(self) -> None:
        self.assertEqual([], validate_text_handoff(self.text_handoff, locked_shot_map=self.locked))
        self.assertEqual([], self.director_errors(self.text_handoff, self.locked))
        self.assertEqual(compute_shot_map_sha256(self.locked), self.text_handoff["locked_semantic_hash"])

    def test_positive_payload_passes_both_cli_entry_points(self) -> None:
        extract = subprocess.run(
            [
                sys.executable,
                str(EXTRACT_VALIDATOR),
                str(self.text_path),
                "--locked-shot-map",
                str(self.locked_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        director = subprocess.run(
            [
                sys.executable,
                str(DIRECTOR_VALIDATOR),
                "--handoff",
                str(self.text_path),
                "--locked-shot-map",
                str(self.locked_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, extract.returncode, extract.stdout + extract.stderr)
        self.assertEqual(0, director.returncode, director.stdout + director.stderr)

    def test_old_v1_is_rejected_by_both(self) -> None:
        bad = copy.deepcopy(self.text_handoff)
        bad["schema_version"] = "text-handoff-v1.0"
        self.assert_rejected_by_both(bad, self.locked)

    def test_same_skill_linter_api_can_run_structural_check_without_claiming_merge_proof(self) -> None:
        self.assertEqual([], validate_text_handoff(self.text_handoff))
        cli = subprocess.run(
            [sys.executable, str(EXTRACT_VALIDATOR), str(self.text_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(0, cli.returncode)
        self.assertIn("--locked-shot-map", cli.stderr)

    def test_extract_text_gate_rejects_even_valid_image_handoff(self) -> None:
        director_errors = self.director_errors(self.image_handoff, self.locked)
        self.assertEqual([], director_errors)
        self.assertTrue(validate_text_handoff(self.image_handoff, locked_shot_map=self.locked))

    def test_collection_order_or_missing_unit_is_rejected_by_both(self) -> None:
        bad = copy.deepcopy(self.text_handoff)
        bad["collections"]["unit_ids"][1:3] = reversed(bad["collections"]["unit_ids"][1:3])
        self.assert_rejected_by_both(bad, self.locked)

        bad = copy.deepcopy(self.text_handoff)
        bad["source_units"].pop()
        self.assert_rejected_by_both(bad, self.locked)

    def test_missing_time_description_script_and_six_layer_are_rejected(self) -> None:
        for path in (
            ("source_units", 0, "source_timecode"),
            ("source_units", 0, "generation_timecode"),
            ("source_units", 0, "storyboard_description"),
            ("source_units", 0, "script_text"),
        ):
            with self.subTest(field=path[-1]):
                locked = copy.deepcopy(self.locked)
                bad = copy.deepcopy(self.text_handoff)
                del locked[path[0]][path[1]][path[2]]
                relock(bad, locked)
                self.assert_rejected_by_both(bad, locked)

        locked = copy.deepcopy(self.locked)
        bad = copy.deepcopy(self.text_handoff)
        del locked["source_units"][0]["source_performance_layers"]["gaze"]
        relock(bad, locked)
        self.assert_rejected_by_both(bad, locked)

    def test_packaging_visible_faces_are_locked_and_required(self) -> None:
        locked = copy.deepcopy(self.locked)
        bad = copy.deepcopy(self.text_handoff)
        locked["source_units"][1]["packaging_evidence"]["visible_faces"] = []
        relock(bad, locked)
        self.assert_rejected_by_both(bad, locked)

    def test_three_rows_or_images_cannot_impersonate_three_eating_events(self) -> None:
        locked = copy.deepcopy(self.locked)
        bad = copy.deepcopy(self.text_handoff)
        for occurrence in locked["eating_plan"]["occurrences"]:
            occurrence["event_group_id"] = "ONE-EATING-EVENT"
            occurrence["shot_id"] = "S001"
            occurrence["unit_id"] = "SRC001"
            occurrence["origin"] = "source"
            occurrence["source_shot_id"] = "SRC001"
            occurrence.pop("inserted_shot_id", None)
        relock(bad, locked)
        self.assert_rejected_by_both(bad, locked)

    def test_eating_event_binding_timeline_and_non_contiguous_flag_are_required(self) -> None:
        for field in ("unit_id", "timeline_timecode", "non_contiguous_event"):
            with self.subTest(field=field):
                locked = copy.deepcopy(self.locked)
                bad = copy.deepcopy(self.text_handoff)
                del locked["eating_plan"]["occurrences"][1][field]
                relock(bad, locked)
                self.assert_rejected_by_both(bad, locked)

    def test_break_person_and_hands_only_modes_and_crisp_proof_are_required(self) -> None:
        locked = copy.deepcopy(self.locked)
        bad = copy.deepcopy(self.text_handoff)
        locked["break_plan"]["occurrences"].pop()
        relock(bad, locked)
        self.assert_rejected_by_both(bad, locked)

        locked = copy.deepcopy(self.locked)
        bad = copy.deepcopy(self.text_handoff)
        locked["break_plan"]["occurrences"][0]["crisp_proof"]["crumbs"] = {
            "minimum": 0,
            "maximum": 0,
        }
        relock(bad, locked)
        self.assert_rejected_by_both(bad, locked)

    def test_natural_language_alignment_table_is_forbidden(self) -> None:
        bad = copy.deepcopy(self.text_handoff)
        bad["alignment_table"] = "人工对齐表"
        self.assert_rejected_by_both(bad, self.locked)


if __name__ == "__main__":
    unittest.main(verbosity=2)
