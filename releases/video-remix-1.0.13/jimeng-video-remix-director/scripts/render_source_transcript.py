#!/usr/bin/env python3
"""Render the editable source transcript for direct user-facing paste."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from validate_source_intake_handoff import validate


def main() -> int:
    parser = argparse.ArgumentParser(description="Print a source transcript, not its internal handoff path.")
    parser.add_argument("--handoff", required=True, type=Path)
    args = parser.parse_args()
    value = json.loads(args.handoff.expanduser().resolve().read_text(encoding="utf-8"))
    errors = validate(value)
    if errors:
        raise SystemExit("Source transcript render refused: " + "; ".join(errors))
    if value.get("branch_role") != "text":
        raise SystemExit("Source transcript render requires branch_role=text")
    editable_text = value["transcript"]["editable_text"].strip()
    print("原片口播（可直接修改）：\n")
    print(editable_text)
    print("\n请直接在这版上修改或确认；确认后我继续锁定分镜、图文合并并按你要求交付。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
