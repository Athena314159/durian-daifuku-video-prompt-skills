#!/usr/bin/env python3
"""Lint the mechanical and role-lock rules of a compiled video-prompt TXT.

The script deliberately does not claim that content review is complete. It can
find encodable omissions and contradictions; a human still has to compare the
source video, audio, revised script, and generated pixels.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SHOT_RE = re.compile(r"(?m)^S\d{3}｜.*$")
PROMPT_RE = re.compile(
    r"【完整Prompt(?:｜主体非空白字符数：\d+)?】\s*(.*?)(?=\n【(?:原片动作对应|内容审核记录)】|\n={10,}|\Z)",
    re.S,
)
PROMPT_COUNT_RE = re.compile(r"【完整Prompt｜主体非空白字符数：(\d+)】")
SCRIPT_RE = re.compile(r"【口播稿】\s*(.*?)(?=\n【完整Prompt|\n={10,}|\Z)", re.S)
FIELD_RE = re.compile(r"(?m)^(?P<name>人物位置|声音方式|产品形态)：(?P<value>.*)$")
TIME_RE = re.compile(r"(?m)^\s*0\.00(?:0)?\s*(?:秒)?\s*[–—-]")
DIALOGUE_RE = re.compile(r"(?m)^\s*([^：\n]+)：[“\"](.*?)[”\"]\s*$")
STATUS_RE = re.compile(r"(?i)(?<![A-Za-z])(PASS|FAIL|ERROR)(?![A-Za-z])")
INTERNAL_PRODUCT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:V[1-5]|whole|bitten|person_eating|hand_tear|cut_open|two_halves)(?![A-Za-z0-9_])"
)
GENERIC_SPEECH_PLACEHOLDERS = (
    "沿用原片生活口语节奏",
    "沿用原片生活口语风格",
    "沿用原片口语节奏",
    "保持原片口语感",
)
ACCENT_SPECIFIC_TERMS = (
    "北方",
    "东北",
    "京腔",
    "山东",
    "河南",
    "川渝",
    "四川",
    "重庆",
    "江浙",
    "吴语",
    "粤语",
    "广府",
    "闽南",
    "台湾",
    "港式",
    "湖南",
    "湖北",
    "平翘舌",
    "前后鼻音",
    "卷舌",
    "儿化",
    "鼻音",
)

NO_TEXT_GROUPS = (
    ("禁止", "严禁", "不得", "不出现", "不能出现"),
    ("字幕", "对白文字", "自动字幕"),
    ("水印",),
)
PERFORMANCE_GROUPS = {
    "视频核心": ("核心", "叙事作用", "真正要表达", "情绪回报点", "情绪落点"),
    "视线轨迹": ("视线", "目光", "看向", "抬眼", "回看"),
    "五官微反应": ("眉峰", "眉眼", "眼睑", "瞳孔", "鼻翼", "嘴角", "下巴", "喉结"),
    "身体或手部准备": ("肩", "重心", "手指", "手腕", "掌根", "指腹", "身体", "上身"),
    "呼吸或停顿": ("呼吸", "吸气", "呼气", "换气", "停顿", "半拍", "四分之一秒", "鼻息"),
    "讲话细节": ("语速", "重音", "尾音", "起音", "音量", "口吻", "口语", "连读", "断句", "音色"),
}
BITE_CHAIN_GROUPS = {
    "接近口部": ("送向", "接近", "靠近"),
    "张口或咬合": ("张嘴", "张口", "咬合", "咬入"),
    "产品离嘴或撤回": ("离嘴", "撤回", "抽离", "移开"),
    "形成咬口或断面": ("咬口", "断面"),
    "咀嚼或吞咽": ("咀嚼", "吞咽"),
}
VISIBLE_TERMS = ("出现", "出镜", "进入画面", "露出", "可见", "纳入画面", "展示")
BODY_TERMS = ("脸", "侧脸", "嘴", "头发", "头部", "身体", "影子", "倒影", "自拍")
NEGATIVE_TERMS = ("不出现", "不出镜", "不得", "禁止", "严禁", "不能", "不可", "避免", "不生成", "绝不")
BUTTER_CRISP_PRODUCT_TERMS = ("黄油脆丝棒", "脆丝棒")
BUTTER_CRISP_BARE_STATE_TERMS = ("完整未破", "开袋并露出", "手持", "摆盘", "掰断", "断面", "咬食", "咬口", "碎屑")
BUTTER_CRISP_MATERIAL_GROUPS = {
    "实体片状覆盖层": ("实体片状", "片状脆丝", "片状碎片", "实体脆丝碎片", "实体材料层"),
    "独立厚度": ("自身厚度", "独立厚度", "侧边厚度"),
    "遮挡与缝隙": ("前后遮挡", "互相遮挡", "重叠缝隙", "不规则窄缝", "暗缝"),
    "轮廓凸出": ("轮廓凸出", "凸出并打破", "打破长边轮廓", "打破干净外轮廓"),
    "平面图案排除": ("平面贴图", "印刷图案", "浅浮雕", "压花", "光滑橙色基底"),
}
BUTTER_CRISP_BOX_STATE_TERMS = ("零售外盒", "外盒", "三盒", "两盒", "一盒", "盒体", "包装盒")
BUTTER_CRISP_BOX_DIMENSION_TERMS = ("15 × 15 × 4.5", "15×15×4.5", "15*15*4.5")
BUTTER_CRISP_BOX_RATIO_TERMS = ("1:1", "正方形正面", "正面近似正方形")
BUTTER_CRISP_BOX_DEPTH_TERMS = ("30%", "0.3边长", "0.3 边长", "厚度约为正面边长")


@dataclass(frozen=True)
class Issue:
    code: str
    message: str


def split_shots(text: str) -> list[tuple[str, str]]:
    matches = list(SHOT_RE.finditer(text))
    return [
        (
            match.group(0).strip(),
            text[match.start() : matches[i + 1].start()] if i + 1 < len(matches) else text[match.start() :],
        )
        for i, match in enumerate(matches)
    ]


def shot_id(title: str) -> str:
    return title.split("｜", 1)[0]


def field(block: str, name: str) -> str:
    for match in FIELD_RE.finditer(block):
        if match.group("name") == name:
            return match.group("value").strip()
    return ""


def normalize_text(value: str) -> str:
    return re.sub(r"[\s，。！？!?；;、“”\"：:…]+", "", value)


def dialogue_chunks(value: str) -> list[str]:
    """Return spoken chunks that may be separated by performance narration."""
    raw_chunks = [normalize_text(part) for part in re.split(r"[，。！？!?；;、…]+", value) if normalize_text(part)]
    chunks: list[str] = []
    for chunk in raw_chunks:
        if len(chunk) > 2 and chunk[0] in "哇诶哎啊欸哦嗯":
            chunks.extend((chunk[0], chunk[1:]))
        else:
            chunks.append(chunk)
    return chunks


def script_lines(block: str) -> list[tuple[str, str]]:
    match = SCRIPT_RE.search(block)
    if not match or match.group(1).strip() == "无":
        return []
    return [(m.group(1).strip(), m.group(2).strip()) for m in DIALOGUE_RE.finditer(match.group(1))]


def has_no_text_rule(prompt: str) -> bool:
    return all(any(term in prompt for term in group) for group in NO_TEXT_GROUPS)


def segment_is_positive_visibility(segment: str, label: str) -> bool:
    if label not in segment:
        return False
    if any(term in segment for term in NEGATIVE_TERMS):
        return False
    return any(term in segment for term in VISIBLE_TERMS) and any(term in segment for term in BODY_TERMS)


def load_role_lock(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    with path.open(encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("角色锁根节点必须是对象")
    return data


def lint_shot(title: str, block: str) -> tuple[list[Issue], str, list[tuple[str, str]]]:
    sid = shot_id(title)
    issues: list[Issue] = []
    required = (
        "原片时间：",
        "独立生成时长：",
        "人物位置：",
        "声音方式：",
        "产品形态：",
        "生成首帧：",
        "【口播稿】",
        "【原片动作对应】",
        "【内容审核记录】",
    )
    for marker in required:
        if marker not in block:
            issues.append(Issue("TXT_EXPORT_MISSING", f"{sid} 缺少 {marker}"))

    prompt_match = PROMPT_RE.search(block)
    if not prompt_match:
        issues.append(Issue("TXT_EXPORT_MISSING", f"{sid} 缺少完整 Prompt 主体"))
        return issues, "", script_lines(block)

    prompt = prompt_match.group(1).strip()
    char_count = len(re.sub(r"\s+", "", prompt))
    if char_count < 3000:
        issues.append(Issue("PROMPT_TOO_SHORT", f"{sid} Prompt 只有 {char_count} 个非空白字符，完整编译至少需要 3000 个"))
    if char_count > 4000:
        issues.append(Issue("PROMPT_TOO_LONG", f"{sid} Prompt 有 {char_count} 个非空白字符"))
    count_match = PROMPT_COUNT_RE.search(block)
    if not count_match or int(count_match.group(1)) != char_count:
        stated = count_match.group(1) if count_match else "缺失"
        issues.append(Issue("PROMPT_CHAR_COUNT_MISMATCH", f"{sid} 标注字符数为 {stated}，程序实算为 {char_count}"))
    if not TIME_RE.search(prompt):
        issues.append(Issue("GENERATION_TIME_NOT_ZERO", f"{sid} 未找到从 0.00 秒开始的动作时间"))
    if not has_no_text_rule(prompt):
        issues.append(Issue("NO_TEXT_RULE_MISSING", f"{sid} Prompt 缺少禁止字幕和水印的硬规则"))

    product_state = field(block, "产品形态")
    if INTERNAL_PRODUCT_RE.search(product_state):
        issues.append(Issue("PRODUCT_STATE_LABEL_NOT_CHINESE", f"{sid} 产品形态使用了内部英文或字母标签：{product_state}"))

    is_butter_crisp = any(term in product_state or term in prompt for term in BUTTER_CRISP_PRODUCT_TERMS)
    if is_butter_crisp and any(term in product_state for term in BUTTER_CRISP_BARE_STATE_TERMS):
        for label, words in BUTTER_CRISP_MATERIAL_GROUPS.items():
            if not any(word in prompt for word in words):
                issues.append(
                    Issue(
                        "PRODUCT_MICROSTRUCTURE_RULE_MISSING",
                        f"{sid} 黄油脆丝棒裸产品 Prompt 缺少{label}规则，可能退化成表面图案",
                    )
                )
    if is_butter_crisp and any(term in product_state for term in BUTTER_CRISP_BOX_STATE_TERMS):
        if not any(term in prompt for term in BUTTER_CRISP_BOX_DIMENSION_TERMS):
            issues.append(Issue("PACKAGE_DIMENSION_LOCK_MISSING", f"{sid} 黄油脆丝棒外盒缺少 15×15×4.5 cm 尺寸锁"))
        if not any(term in prompt for term in BUTTER_CRISP_BOX_RATIO_TERMS):
            issues.append(Issue("PACKAGE_DIMENSION_LOCK_MISSING", f"{sid} 黄油脆丝棒外盒缺少 1:1 正方形正面约束"))
        if not any(term in prompt for term in BUTTER_CRISP_BOX_DEPTH_TERMS):
            issues.append(Issue("PACKAGE_DIMENSION_LOCK_MISSING", f"{sid} 黄油脆丝棒外盒缺少约 0.3 边长盒厚约束"))

    status = STATUS_RE.search(block)
    if status:
        issues.append(
            Issue(
                "STRUCTURE_RESULT_MISREPRESENTED_AS_CONTENT_AUDIT",
                f"{sid} 使用了状态词 {status.group(1)}；请改写为具体人物、台词、动作或待看像素事实",
            )
        )

    lines = script_lines(block)
    for phrase in GENERIC_SPEECH_PLACEHOLDERS:
        if phrase in prompt:
            issues.append(
                Issue(
                    "GENERIC_SPEECH_PLACEHOLDER",
                    f"{sid} 使用了泛化讲话占位表达“{phrase}”；请改写为具体口音、语音和节奏方案",
                )
            )
    if lines and not any(term in prompt for term in ACCENT_SPECIFIC_TERMS):
        issues.append(Issue("ACCENT_PLAN_MISSING", f"{sid} 有口播，但 Prompt 没有具体地域口语方向或语音特征"))

    performance_groups = dict(PERFORMANCE_GROUPS)
    if not lines:
        performance_groups["无口播镜头的现场声音"] = ("声音", "环境底噪", "摩擦", "呼吸", "轻响")
        performance_groups.pop("讲话细节", None)
    for label, words in performance_groups.items():
        if not any(word in prompt for word in words):
            issues.append(Issue("PERFORMANCE_DETAIL_MISSING", f"{sid} 缺少可观察的{label}"))

    for label, text in lines:
        normalized_prompt = normalize_text(prompt)
        if not all(chunk in normalized_prompt for chunk in dialogue_chunks(text)):
            issues.append(Issue("SCRIPT_OMITTED", f"{sid} 的完整 Prompt 未叙事性写入 {label} 台词：{text}"))

    action_section = block.split("【原片动作对应】", 1)[-1].split("【内容审核记录】", 1)[0]
    if not re.search(r"\d{1,2}:\d{2}(?:\.\d+)?", action_section) or not any(
        term in action_section for term in ("对应", "复刻", "映射")
    ):
        issues.append(Issue("SOURCE_ACTION_OMITTED", f"{sid} 原片动作对应未写可核对的时间与映射事实"))

    audit_section = block.split("【内容审核记录】", 1)[-1]
    if not any(term in audit_section for term in ("人物", "角色")):
        issues.append(Issue("TXT_EXPORT_MISSING", f"{sid} 内容审核记录缺人物事实"))
    if lines and not any(term in audit_section for term in ("台词", "口型", "声音", "声源")):
        issues.append(Issue("TXT_EXPORT_MISSING", f"{sid} 内容审核记录缺台词/声源事实"))

    return issues, prompt, lines


def lint_role_lock(
    shots: list[tuple[str, str]],
    prompts: dict[str, str],
    all_script_lines: list[tuple[str, str, str]],
    role_lock: dict[str, Any],
) -> list[Issue]:
    issues: list[Issue] = []
    characters = role_lock.get("characters")
    dialogue = role_lock.get("dialogue")
    if not isinstance(characters, dict) or not isinstance(dialogue, list):
        return [Issue("SPEAKER_IDENTITY_UNCONFIRMED", "角色锁必须包含 characters 对象和 dialogue 数组")]

    if dialogue:
        speech_plan = role_lock.get("speech_plan")
        if not isinstance(speech_plan, dict) or not str(speech_plan.get("summary", "")).strip():
            issues.append(Issue("ACCENT_PLAN_MISSING", "角色锁有台词，但缺少 speech_plan 具体讲话方案"))
        else:
            summary = str(speech_plan.get("summary", ""))
            for phrase in GENERIC_SPEECH_PLACEHOLDERS:
                if phrase in summary:
                    issues.append(Issue("GENERIC_SPEECH_PLACEHOLDER", f"角色锁 speech_plan 使用了泛化占位表达“{phrase}”"))
            if not any(term in summary for term in ACCENT_SPECIFIC_TERMS):
                issues.append(Issue("ACCENT_PLAN_MISSING", "角色锁 speech_plan 缺具体地域口语方向或语音特征"))
            if speech_plan.get("source") == "creative_proposal" and speech_plan.get("disclosed_to_user") is not True:
                issues.append(Issue("ACCENT_PROPOSAL_NOT_DISCLOSED", "口音来源是创作提案，但角色锁没有记录已在对话中告知用户"))

    delivered = {(normalize_text(label), normalize_text(text)): sid for sid, label, text in all_script_lines}
    expected_texts: dict[str, str] = {}
    for item in dialogue:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        text = str(item.get("text", "")).strip()
        if not label or not text:
            issues.append(Issue("SPEAKER_IDENTITY_UNCONFIRMED", "角色锁 dialogue 条目缺 label 或 text"))
            continue
        key = (normalize_text(label), normalize_text(text))
        if key not in delivered:
            wrong = [actual_label for (actual_label, actual_text) in delivered if actual_text == normalize_text(text)]
            if wrong:
                issues.append(Issue("DIALOGUE_SPEAKER_MISMATCH", f"台词“{text}”应由{label}说，TXT 中标成了{wrong[0]}"))
            else:
                issues.append(Issue("SCRIPT_OMITTED", f"角色锁台词未出现在任何分镜口播稿中：{label}“{text}”"))
        expected_texts[normalize_text(text)] = normalize_text(label)

    for sid, actual_label, text in all_script_lines:
        expected_label = expected_texts.get(normalize_text(text))
        if expected_label is not None and normalize_text(actual_label) != expected_label:
            issues.append(Issue("DIALOGUE_SPEAKER_MISMATCH", f"{sid} 台词“{text}”说话人应为{expected_label}，实际为{actual_label}"))

    for character in characters.values():
        if not isinstance(character, dict):
            continue
        label = str(character.get("label", "")).strip()
        visibility = character.get("visibility")
        if not label:
            issues.append(Issue("SPEAKER_IDENTITY_UNCONFIRMED", "角色锁 characters 条目缺 label"))
            continue
        for title, block in shots:
            sid = shot_id(title)
            position = field(block, "人物位置")
            prompt = prompts.get(sid, "")
            if visibility == "offscreen_all":
                if label not in position or not any(term in position for term in ("镜头后", "镜外", "不出镜")):
                    issues.append(Issue("OFFSCREEN_CHARACTER_VISIBLE", f"{sid} 人物位置没有继承“{label}全程镜外”"))
                for segment in re.split(r"[。！？!?；;\n]", prompt):
                    if segment_is_positive_visibility(segment, label):
                        excerpt = segment.strip()[:80]
                        issues.append(Issue("OFFSCREEN_CHARACTER_VISIBLE", f"{sid} 安排全程镜外的{label}出镜：{excerpt}"))
                        break
            elif visibility == "onscreen_all":
                if label not in position or not any(term in position for term in ("画面中", "镜头中", "在画面")):
                    issues.append(Issue("SPEAKER_IDENTITY_CONFLICT", f"{sid} 人物位置没有继承“{label}全程画面中”"))

    for item in role_lock.get("required_actions", []):
        if not isinstance(item, dict):
            continue
        sid = str(item.get("shot_id", ""))
        kind = item.get("kind")
        prompt = prompts.get(sid, "")
        if kind == "bite_chain":
            for label, words in BITE_CHAIN_GROUPS.items():
                if not any(word in prompt for word in words):
                    issues.append(Issue("SOURCE_ACTION_OMITTED", f"{sid} 原片含咬食，但 Prompt 缺少咬食链环节：{label}"))

    return issues


def lint(path: Path, role_lock_path: Path | None = None) -> list[Issue]:
    text = path.read_text(encoding="utf-8-sig")
    shots = split_shots(text)
    if not shots:
        return [Issue("TXT_EXPORT_MISSING", "未找到 S001｜… 形式的分镜标题")]

    issues: list[Issue] = []
    prompts: dict[str, str] = {}
    all_lines: list[tuple[str, str, str]] = []
    for title, block in shots:
        shot_issues, prompt, lines = lint_shot(title, block)
        issues.extend(shot_issues)
        sid = shot_id(title)
        prompts[sid] = prompt
        all_lines.extend((sid, label, text) for label, text in lines)

    role_lock = load_role_lock(role_lock_path)
    if role_lock is not None:
        issues.extend(lint_role_lock(shots, prompts, all_lines, role_lock))

    seen: set[tuple[str, str]] = set()
    unique: list[Issue] = []
    for issue in issues:
        key = (issue.code, issue.message)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(description="检查逐分镜 Prompt 的结构和可编码的角色锁冲突")
    parser.add_argument("txt", type=Path)
    parser.add_argument("--role-lock", type=Path, default=None)
    args = parser.parse_args()
    if not args.txt.is_file():
        print(f"文件不存在：{args.txt}", file=sys.stderr)
        return 2
    if args.role_lock is not None and not args.role_lock.is_file():
        print(f"角色锁不存在：{args.role_lock}", file=sys.stderr)
        return 2
    try:
        issues = lint(args.txt, args.role_lock)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"无法检查：{exc}", file=sys.stderr)
        return 2
    if issues:
        print(f"发现结构性阻断：{len(issues)}项")
        for issue in issues:
            print(f"- [{issue.code}] {issue.message}")
        return 1
    print("未发现结构性阻断。此结果只说明可自动检查的结构与角色锁一致，不代表内容审核完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
