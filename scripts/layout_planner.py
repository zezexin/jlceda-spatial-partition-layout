#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""嘉立创EDA空间预演器：只规划坐标与页数，不在EDA中绘制分区框。

输入JSON示例见 examples/layout_plan.example.json。
A4/A5必须从当前EDA页面读取并传入真实 safe_area/title_block_exclusion，禁止缩放猜测A3坐标。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

COLOR_PALETTE = [
    "#16a34a", "#dc2626", "#ff5701", "#60a5fa", "#e00b41", "#460479",
    "#ff385c", "#8b5cf6", "#dc2626", "#2997ff", "#0066cc", "#0071e3",
    "#9333ea", "#16f2b3", "#e84393", "#ff5757", "#00c4cc", "#b53333",
    "#e60076", "#c800df",
]

A3_PROFILE = {
    "safe_area": {"x_min": 40, "y_min": 200, "x_max": 1615, "y_max": 1145},
    "title_block_exclusion": {"x_min": 945, "y_min": 7, "x_max": 1645, "y_max": 190},
}
SUPPORTED_SIZES = {"A3", "A4", "A5"}


def _validate_box(box: Dict[str, float], label: str) -> None:
    required = ("x_min", "y_min", "x_max", "y_max")
    if not isinstance(box, dict) or any(k not in box for k in required):
        raise ValueError(f"{label}必须包含 x_min/y_min/x_max/y_max。")
    if float(box["x_max"]) <= float(box["x_min"]) or float(box["y_max"]) <= float(box["y_min"]):
        raise ValueError(f"{label}坐标无效：最大值必须大于最小值。")


def _color_sequence(count: int) -> List[str]:
    """随机洗牌颜色；相邻分区尽量不重复颜色，超出调色板后重新洗牌。"""
    rng = random.SystemRandom()
    result: List[str] = []
    bag: List[str] = []
    while len(result) < count:
        if not bag:
            bag = COLOR_PALETTE.copy()
            rng.shuffle(bag)
            if result and len(bag) > 1 and bag[-1] == result[-1]:
                bag[-1], bag[0] = bag[0], bag[-1]
        color = bag.pop()
        if result and color == result[-1] and bag:
            swap_idx = next((i for i, c in enumerate(bag) if c != result[-1]), None)
            if swap_idx is not None:
                bag[swap_idx], bag[-1] = bag[-1], bag[swap_idx]
                color = bag.pop()
        result.append(color)
    return result


def plan_regions(spec: Dict[str, Any]) -> Dict[str, Any]:
    """按模块估算宽高进行简单货架排布，空间不足自动分配新页。

    每个block必须提供 name/width/height；priority越小越先安排。宽高必须由当前图幅
    与器件/连线预估得出，不能用缩小器件尺寸来“凑版面”。
    """
    sheet_size = str(spec.get("sheet_size", "")).upper().strip()
    if sheet_size not in SUPPORTED_SIZES:
        raise ValueError("必须明确 sheet_size 为 A3、A4 或 A5。")
    if spec.get("sheet_size_confirmed") is not True:
        raise ValueError("图幅尚未由用户确认：先报告识别的图幅并取得确认，再生成布局预演。")

    if "safe_area" in spec and spec["safe_area"]:
        safe_area = dict(spec["safe_area"])
    elif sheet_size == "A3":
        safe_area = dict(A3_PROFILE["safe_area"])
    else:
        raise ValueError(f"{sheet_size}没有已校准的安全区：请从当前EDA页面读取真实坐标并填入 safe_area，不能按比例推算A3坐标。")
    _validate_box(safe_area, "safe_area")

    if spec.get("title_block_exclusion"):
        title_block_exclusion = dict(spec["title_block_exclusion"])
    elif sheet_size == "A3":
        title_block_exclusion = dict(A3_PROFILE["title_block_exclusion"])
    else:
        raise ValueError(f"{sheet_size}没有已校准的标题栏禁布区：请从当前EDA页面读取后填入 title_block_exclusion。")
    _validate_box(title_block_exclusion, "title_block_exclusion")

    gap = float(spec.get("gap", 50))
    if gap < 50:
        raise ValueError("功能区之间的间距不得低于50 units；如版面放不下，应增加页面。")

    blocks = spec.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("blocks必须是非空列表，每项包含name、width和height。")
    names = [str(b.get("name", "")).strip() for b in blocks]
    if any(not name for name in names):
        raise ValueError("每个功能块必须有非空name。")
    if len(set(names)) != len(names):
        raise ValueError("功能块name不能重复。")

    xmin, ymin = float(safe_area["x_min"]), float(safe_area["y_min"])
    xmax, ymax = float(safe_area["x_max"]), float(safe_area["y_max"])
    max_w, max_h = xmax - xmin, ymax - ymin
    normalized: List[Tuple[int, Dict[str, Any], float, float, int]] = []
    for index, block in enumerate(blocks):
        try:
            width, height = float(block["width"]), float(block["height"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"功能块 {block.get('name', '?')} 必须提供有效的width/height估算。") from exc
        if width <= 0 or height <= 0:
            raise ValueError(f"功能块 {block['name']} 的宽高必须大于0。")
        if width > max_w or height > max_h:
            raise ValueError(
                f"单个功能块“{block['name']}”估算尺寸 {width:g}×{height:g} 超过 {sheet_size} 安全区 "
                f"{max_w:g}×{max_h:g}。请拆分该功能块或确认更大图幅；仅新增同尺寸页无法解决单块超限。"
            )
        try:
            priority = int(block.get("priority", 100))
        except (TypeError, ValueError):
            priority = 100
        normalized.append((index, block, width, height, priority))

    # 低数字优先；同优先级优先排放高/大的模块，减少碎片；最终位置会写入输入顺序序号供回溯。
    normalized.sort(key=lambda item: (item[4], -item[3], -item[2], item[0]))

    raw_regions: List[Dict[str, Any]] = []
    pages_internal: List[Dict[str, Any]] = []
    page_no = 1
    cursor_x, cursor_y = xmin, ymin
    row_height = 0.0

    def fresh_page(number: int) -> Dict[str, Any]:
        return {
            "page": number,
            "sheet_size": sheet_size,
            "safe_area": safe_area,
            "title_block_exclusion": title_block_exclusion,
            "regions": {},
            "components": [],
            "titles": [],
            "wires": [],
            "planning_only": True,
        }

    pages_internal.append(fresh_page(page_no))
    for source_index, block, width, height, priority in normalized:
        # 当前行放不下时，换行。
        if cursor_x + width > xmax:
            cursor_x = xmin
            cursor_y += row_height + gap
            row_height = 0.0
        # 当前页高度不够时，开启新页；不缩小该功能块。
        if cursor_y + height > ymax:
            page_no += 1
            pages_internal.append(fresh_page(page_no))
            cursor_x, cursor_y, row_height = xmin, ymin, 0.0
        # 如果新行/新页仍无法放置，报错而非静默越界。
        if cursor_x + width > xmax or cursor_y + height > ymax:
            raise ValueError(f"功能块“{block['name']}”无法放进安全区；请拆块或确认更大图幅。")

        region = {
            "x_min": cursor_x,
            "y_min": cursor_y,
            "x_max": cursor_x + width,
            "y_max": cursor_y + height,
            "estimated_width": width,
            "estimated_height": height,
            "source_order": source_index,
            "priority": priority,
        }
        pages_internal[-1]["regions"][block["name"]] = region
        raw_regions.append(region)
        cursor_x += width + gap
        row_height = max(row_height, height)

    colors = _color_sequence(len(raw_regions))
    color_idx = 0
    for page in pages_internal:
        for region in page["regions"].values():
            region["stroke_color"] = colors[color_idx]
            color_idx += 1

    return {
        "skill": "jlceda-spatial-partition-layout",
        "plan_version": 2,
        "sheet_size": sheet_size,
        "sheet_size_confirmed": True,
        "gap": gap,
        "planning_only": True,
        "visible_frames_must_wait_until_wiring_complete": True,
        "pages": pages_internal,
        "decision": {
            "page_count": len(pages_internal),
            "requires_additional_pages": len(pages_internal) > 1,
            "note": (
                "这是空间预演，不要立即在EDA中绘制分区框。先按计划放置器件并完成连线，"
                "再根据真实包围盒重新计算分区框与标题。"
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="布局规格JSON文件；- 表示从stdin读取")
    parser.add_argument("-o", "--output", help="输出JSON路径；缺省输出到stdout")
    args = parser.parse_args()
    try:
        raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
        spec = json.loads(raw)
        result = plan_regions(spec)
        serialized = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(serialized + "\n", encoding="utf-8")
        else:
            print(serialized)
        return 0
    except Exception as exc:  # CLI须输出可行动的失败原因
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
