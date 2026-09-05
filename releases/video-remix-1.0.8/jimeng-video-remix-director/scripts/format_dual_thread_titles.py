#!/usr/bin/env python3
"""Generate exact paired sidebar titles for image/text video tasks."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo


SEPARATOR = "｜"


def normalize_topic(value: str) -> str:
    topic = value.strip()
    topic = re.sub(r"^\d{1,2}[.月/-]\d{1,2}(?:日)?\s*", "", topic)
    topic = re.sub(r"[|｜:：]+", " ", topic)
    topic = re.sub(r"(?:图|文)\s*[Aa]gent$", "", topic, flags=re.IGNORECASE)
    topic = re.sub(r"(?:新对话|agent视频|Agent视频|视频视觉线|视频文案线)$", "", topic)
    topic = re.sub(r"\s+", "", topic)
    if not topic:
        raise ValueError("任务主题不能为空")
    return topic[:20]


def date_label(iso_date: str | None) -> str:
    if iso_date:
        value = datetime.strptime(iso_date, "%Y-%m-%d")
    else:
        value = datetime.now(ZoneInfo("Asia/Shanghai"))
    return f"{value.month}.{value.day:02d}"


def build_titles(topic: str, iso_date: str | None = None) -> dict[str, str]:
    clean_topic = normalize_topic(topic)
    prefix = date_label(iso_date)
    return {
        "topic": clean_topic,
        "image_title": f"{prefix}{SEPARATOR}{clean_topic}{SEPARATOR}图Agent",
        "text_title": f"{prefix}{SEPARATOR}{clean_topic}{SEPARATOR}文Agent",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", required=True)
    parser.add_argument("--date", help="YYYY-MM-DD；默认使用上海时区今天")
    args = parser.parse_args()
    print(json.dumps(build_titles(args.topic, args.date), ensure_ascii=False))


if __name__ == "__main__":
    main()
