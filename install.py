#!/usr/bin/env python3
"""Install the bundled Codex skills without silently overwriting local work."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


SKILLS = (
    "extract-skill",
    "director-skill",
    "product-skill",
    "video-remix-system-v2-canonical",
    "video-remix-system-v3-canonical",
)

LEGACY_SKILLS = (
    "extract-video-prompt",
    "jimeng-video-remix-director",
    "durian-daifuku-five-states",
)


def default_target() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    base = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    return base / "skills"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install the video product-insertion Codex skill bundle.")
    parser.add_argument("--target", type=Path, default=default_target(), help="Codex skills directory")
    parser.add_argument("--force", action="store_true", help="replace existing skills after making backups")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root = Path(__file__).resolve().parent / "skills"
    target_root = args.target.expanduser().resolve()

    missing = [name for name in SKILLS if not (source_root / name / "SKILL.md").is_file()]
    if missing:
        print(f"安装包不完整，缺少：{', '.join(missing)}", file=sys.stderr)
        return 2

    existing = [target_root / name for name in (*SKILLS, *LEGACY_SKILLS) if (target_root / name).exists()]
    if existing and not args.force:
        print("检测到同名 Skill，未进行任何复制：", file=sys.stderr)
        for destination in existing:
            print(f"- {destination}", file=sys.stderr)
        print("确认升级请重新执行并添加 --force。", file=sys.stderr)
        return 1

    target_root.mkdir(parents=True, exist_ok=True)
    backups: list[tuple[Path, Path]] = []
    try:
        for destination in existing:
            backup = target_root / f".{destination.name}.pre-install-backup"
            if backup.exists():
                print(f"已有备份目录，无法安全覆盖：{backup}", file=sys.stderr)
                return 1
            destination.rename(backup)
            backups.append((destination, backup))

        for name in SKILLS:
            shutil.copytree(source_root / name, target_root / name)
    except Exception:
        for name in SKILLS:
            destination = target_root / name
            if destination.exists() and all(destination != original for original, _ in backups):
                shutil.rmtree(destination)
        for original, backup in reversed(backups):
            if backup.exists():
                backup.rename(original)
        raise

    for _, backup in backups:
        shutil.rmtree(backup)

    print("安装完成：")
    for name in SKILLS:
        print(f"- {target_root / name}")
    print("请重新启动 Codex 后再调用 Skill。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
