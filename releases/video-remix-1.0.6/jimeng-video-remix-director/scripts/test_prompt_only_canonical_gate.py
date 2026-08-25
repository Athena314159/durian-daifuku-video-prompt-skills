#!/usr/bin/env python3
"""Named release regression for the prompt-only canonical-delivery gate."""

from __future__ import annotations

from self_test import main as run_end_to_end_contract


def main() -> int:
    result = run_end_to_end_contract()
    assert result == 0
    print("PROMPT-ONLY CANONICAL GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
