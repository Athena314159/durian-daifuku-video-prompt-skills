#!/usr/bin/env python3
"""Render a validated source-intake image handoff as an inline Markdown gallery."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from validate_source_intake_handoff import load_object, source_id_number, validate


def format_timecode(seconds: float) -> str:
    milliseconds = int(round(float(seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"
    return f"{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def escape_alt_text(value: str) -> str:
    return " ".join(value.split()).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def markdown_destination(value: str) -> str:
    # Plain absolute paths produce the exact desktop-app form ![caption](/path).
    # Angle brackets keep paths containing Markdown delimiters clickable.
    if any(character.isspace() for character in value) or any(character in value for character in "()"):
        return f"<{value.replace('>', '%3E')}>"
    return value


def render(value: dict[str, Any]) -> str:
    if value.get("branch_role") != "image":
        raise ValueError("source gallery requires an image-branch handoff")
    if value.get("status") != "source_inventory_ready":
        raise ValueError("source gallery requires status=source_inventory_ready")

    shots = list(value["source_inventory"]["source_shots"])
    shots.sort(key=lambda item: source_id_number(item.get("source_shot_id")) or -1)
    lines: list[str] = []
    for shot in shots:
        timecode = shot["timecode"]
        label = (
            f"{shot['source_shot_id']}｜"
            f"{format_timecode(timecode['start'])}–{format_timecode(timecode['end'])}｜"
            f"{shot['caption'].strip()}"
        )
        lines.append(f"![{escape_alt_text(label)}]({markdown_destination(shot['image_path'])})")
    return "\n\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render every source-intake SRC frame as inline Markdown.")
    parser.add_argument("--handoff", required=True, type=Path)
    args = parser.parse_args()
    try:
        value = load_object(args.handoff.expanduser().resolve())
        errors = validate(value)
        if errors:
            raise ValueError("invalid source-intake handoff: " + "; ".join(errors))
        output = render(value)
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
