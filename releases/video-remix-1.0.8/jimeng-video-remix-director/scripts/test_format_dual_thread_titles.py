#!/usr/bin/env python3
"""Regression tests for paired image/text task titles."""

from format_dual_thread_titles import build_titles


def main() -> None:
    cases = [
        ("榴莲大福", "2026-08-22", "8.22｜榴莲大福｜图Agent", "8.22｜榴莲大福｜文Agent"),
        ("8.21 | 麦乐森脆丝棒 | 图 agent", "2026-08-21", "8.21｜麦乐森脆丝棒｜图Agent", "8.21｜麦乐森脆丝棒｜文Agent"),
        ("客厅老女人：文Agent", "2026-08-02", "8.02｜客厅老女人｜图Agent", "8.02｜客厅老女人｜文Agent"),
    ]
    for topic, iso_date, image_title, text_title in cases:
        result = build_titles(topic, iso_date)
        assert result["image_title"] == image_title, result
        assert result["text_title"] == text_title, result
    print("3 paired-title cases passed")


if __name__ == "__main__":
    main()
