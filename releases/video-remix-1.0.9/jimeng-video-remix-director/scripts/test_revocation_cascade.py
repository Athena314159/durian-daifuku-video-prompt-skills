#!/usr/bin/env python3
"""Regression: a rejected image must invalidate downstream compile/Word authorization."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from invalidate_revoked_delivery import invalidate_delivery


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "planning").mkdir()
        (root / "review").mkdir()
        (root / "prompts").mkdir()
        (root / "exports").mkdir()
        (root / "planning" / "workflow_state.json").write_text(json.dumps({"status": "ready_for_word", "next_allowed_actions": ["export_docx"]}), encoding="utf-8")
        (root / "prompts" / "generation_pack.json").write_text(json.dumps({"status": "compiled"}), encoding="utf-8")
        (root / "exports" / "export_manifest.json").write_text(json.dumps({"status": "aligned"}), encoding="utf-8")
        revocation = root / "review" / "image_handoff_revocation.json"
        revocation.write_text(json.dumps({
            "status": "active",
            "revoked_assets": [{"asset_id": "A1", "sha256": "a" * 64}],
            "reject_codes": ["PACKAGE_CONTACT_SHADOW_MISSING_OR_UNCONVINCING"],
        }), encoding="utf-8")

        result = invalidate_delivery(root, revocation)
        assert result["docx_export_authorized"] is False
        workflow = json.loads((root / "planning" / "workflow_state.json").read_text(encoding="utf-8"))
        assert workflow["status"] == "images_revoked"
        assert "export_docx" not in workflow["next_allowed_actions"]
        pack = json.loads((root / "prompts" / "generation_pack.json").read_text(encoding="utf-8"))
        assert pack["status"] == "stale_due_to_revocation"
        export = json.loads((root / "exports" / "export_manifest.json").read_text(encoding="utf-8"))
        assert export["status"] == "stale_due_to_revocation"
    print("revocation cascade regression tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
