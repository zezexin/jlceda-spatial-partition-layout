#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""嘉立创EDA终检门禁。

CLI：python scripts/check_overlap.py project.json
输入schema见examples/project_gate.example.json。项目门禁覆盖：工作流顺序、分区碰撞、
区域/标题/器件边界、元件相互间距、随机描边色、BOM位号和跨区连线方式。
"""
from __future__ import annotations

import json
import math
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

COLOR_PALETTE = {
    "#16a34a", "#dc2626", "#ff5701", "#60a5fa", "#e00b41", "#460479",
    "#ff385c", "#8b5cf6", "#2997ff", "#0066cc", "#0071e3", "#9333ea",
    "#16f2b3", "#e84393", "#ff5757", "#00c4cc", "#b53333", "#e60076", "#c800df",
}
SUPPORTED_SIZES = {"A3", "A4", "A5"}


def _valid_box(box: Dict[str, Any]) -> bool:
    keys = ("x_min", "y_min", "x_max", "y_max")
    try:
        return all(k in box for k in keys) and float(box["x_max"]) > float(box["x_min"]) and float(box["y_max"]) > float(box["y_min"])
    except (TypeError, ValueError):
        return False


def is_overlap(box1: Dict[str, Any], box2: Dict[str, Any]) -> bool:
    """AABB碰撞检测；仅接触边线不算面积重叠。"""
    if not _valid_box(box1) or not _valid_box(box2):
        return False
    return not (
        float(box1["x_max"]) <= float(box2["x_min"])
        or float(box1["x_min"]) >= float(box2["x_max"])
        or float(box1["y_max"]) <= float(box2["y_min"])
        or float(box1["y_min"]) >= float(box2["y_max"])
    )


def _inside(inner: Dict[str, Any], outer: Dict[str, Any], margin: float = 0) -> bool:
    if not _valid_box(inner) or not _valid_box(outer):
        return False
    return (
        float(inner["x_min"]) >= float(outer["x_min"]) + margin
        and float(inner["y_min"]) >= float(outer["y_min"]) + margin
        and float(inner["x_max"]) <= float(outer["x_max"]) - margin
        and float(inner["y_max"]) <= float(outer["y_max"]) - margin
    )


def _expanded(box: Dict[str, Any], amount: float) -> Dict[str, float]:
    return {
        "x_min": float(box["x_min"]) - amount,
        "y_min": float(box["y_min"]) - amount,
        "x_max": float(box["x_max"]) + amount,
        "y_max": float(box["y_max"]) + amount,
    }


def _distance_between_boxes(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    dx = max(0.0, float(a["x_min"]) - float(b["x_max"]), float(b["x_min"]) - float(a["x_max"]))
    dy = max(0.0, float(a["y_min"]) - float(b["y_max"]), float(b["y_min"]) - float(a["y_max"]))
    return math.hypot(dx, dy)


def estimate_text_width(text: str, font_size: float) -> float:
    """保守估算宽度：东亚宽字符按1em，其他字符按0.62em。"""
    width_em = 0.0
    for char in text:
        width_em += 1.0 if unicodedata.east_asian_width(char) in ("W", "F", "A") else 0.62
    return width_em * font_size


def check_layout(regions: Dict[str, Dict[str, Any]], components: Sequence[Dict[str, Any]], minimum_spacing: float = 20) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    names = list(regions.keys())

    for name, box in regions.items():
        if not _valid_box(box):
            errors.append(f"[FAIL] 分区【{name}】坐标缺失或无效。")

    for i, name_a in enumerate(names):
        for name_b in names[i + 1:]:
            if is_overlap(regions[name_a], regions[name_b]):
                errors.append(f"[FAIL] 分区重叠：【{name_a}】与【{name_b}】相交。")

    valid_components: List[Dict[str, Any]] = []
    for comp in components:
        ref = str(comp.get("ref", "?")).strip()
        if not _valid_box(comp):
            errors.append(f"[FAIL] 器件 {ref} 的包围盒缺失或无效。")
            continue
        valid_components.append(comp)
        region_name = comp.get("region")
        if not region_name:
            warnings.append(f"[WARN] 器件 {ref} 尚未指定功能分区；请确认是否为全局符号/测试点。")
            continue
        reg = regions.get(str(region_name))
        if reg is None:
            errors.append(f"[FAIL] 器件 {ref} 找不到对应分区【{region_name}】。")
            continue
        if not _inside(comp, reg):
            errors.append(f"[FAIL] 器件越界：{ref} 超出分区【{region_name}】。")

    for i, a in enumerate(valid_components):
        for b in valid_components[i + 1:]:
            if _distance_between_boxes(a, b) < minimum_spacing:
                errors.append(
                    f"[FAIL] 器件间距不足/包围盒重叠：{a.get('ref', '?')} 与 {b.get('ref', '?')}，"
                    f"间距约 {_distance_between_boxes(a, b):.1f} units，小于 {minimum_spacing}。"
                )

    return {"status": "FAIL" if errors else "PASS", "messages": errors or ["[PASS] 分区与器件几何检查通过。"], "warnings": warnings}


def check_titles(regions: Dict[str, Dict[str, Any]], titles: Sequence[Dict[str, Any]], margin: float = 10) -> List[str]:
    errors: List[str] = []
    for title in titles:
        name = str(title.get("text", "?"))
        region_name = title.get("region")
        reg = regions.get(str(region_name))
        if not reg:
            errors.append(f"[FAIL] 标题“{name}”找不到所属分区【{region_name}】。")
            continue
        try:
            x, y = float(title["x"]), float(title["y"])
            font_size = float(title.get("font_size", 24))
            width = float(title.get("width", estimate_text_width(name, font_size)))
        except (KeyError, TypeError, ValueError):
            errors.append(f"[FAIL] 标题“{name}”坐标/字号无效。")
            continue
        text_box = {"x_min": x, "y_min": y, "x_max": x + width, "y_max": y + font_size}
        if not _inside(text_box, reg, margin=margin):
            errors.append(
                f"[FAIL] 标题“{name}”没有完整落在分区【{region_name}】内并保留{margin:g} units边距；"
                f"当前文本框 x={x:g}..{x+width:g}, y={y:g}..{y+font_size:g}。"
                f"建议 y_title = y_max - (font_size + 10)。"
            )
    return errors


def _refs(items: Iterable[Any], key: str = "ref") -> List[str]:
    values: List[str] = []
    for item in items:
        if isinstance(item, str):
            value = item.strip()
        elif isinstance(item, dict):
            value = str(item.get(key, "")).strip()
        else:
            value = ""
        if value:
            values.append(value)
    return values


def _duplicates(items: Sequence[str]) -> List[str]:
    seen = set()
    dups = set()
    for item in items:
        if item in seen:
            dups.add(item)
        seen.add(item)
    return sorted(dups)


def check_project(project: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []

    workflow = project.get("workflow", {})
    required_flags = {
        "sheet_size_confirmed": "尚未确认图幅。",
        "components_placed": "尚未标记器件放置完成。",
        "wiring_complete": "尚未标记连线完成。",
        "frames_drawn_after_wiring": "没有确认分区框是在器件/连线完成后绘制。",
    }
    for field, msg in required_flags.items():
        if workflow.get(field) is not True:
            errors.append(f"[FAIL] 工作流门禁：{msg}")

    bom_refs = _refs(project.get("bom_refs", []))
    if "bom_refs" not in project:
        errors.append("[FAIL] 缺少bom_refs，无法宣称位号与BOM一致。")
    elif not bom_refs:
        errors.append("[FAIL] bom_refs为空，无法执行BOM位号校验。")
    for ref in _duplicates(bom_refs):
        errors.append(f"[FAIL] BOM中位号重复：{ref}。")

    pages = project.get("pages")
    if not isinstance(pages, list) or not pages:
        errors.append("[FAIL] 缺少pages页面清单。")
        pages = []

    all_components: List[Dict[str, Any]] = []
    placed_refs: List[str] = []
    for page in pages:
        page_no = page.get("page", "?")
        size = str(page.get("sheet_size", project.get("sheet_size", ""))).upper()
        if size not in SUPPORTED_SIZES:
            errors.append(f"[FAIL] 第{page_no}页图幅未明确为A3/A4/A5（当前：{size or '缺失'}）。")
        safe_area = page.get("safe_area")
        if not _valid_box(safe_area or {}):
            errors.append(f"[FAIL] 第{page_no}页缺少已校准的safe_area。")

        regions = page.get("regions", {})
        components = page.get("components", [])
        titles = page.get("titles", [])
        wires = page.get("wires", [])
        if not isinstance(regions, dict):
            errors.append(f"[FAIL] 第{page_no}页regions必须是对象。")
            regions = {}
        if not isinstance(components, list):
            errors.append(f"[FAIL] 第{page_no}页components必须是列表。")
            components = []
        if not isinstance(titles, list):
            errors.append(f"[FAIL] 第{page_no}页titles必须是列表。")
            titles = []

        layout_result = check_layout(regions, components, minimum_spacing=float(project.get("minimum_component_spacing", 20)))
        errors.extend([f"第{page_no}页：{m}" for m in layout_result["messages"] if m.startswith("[FAIL]")])
        warnings.extend([f"第{page_no}页：{m}" for m in layout_result.get("warnings", [])])
        errors.extend([f"第{page_no}页：{m}" for m in check_titles(regions, titles)])

        reserved_areas = page.get("reserved_areas", [])
        if page.get("title_block_exclusion"):
            reserved_areas = list(reserved_areas) + [page["title_block_exclusion"]]
        for region_name, region in regions.items():
            if safe_area and _valid_box(safe_area) and not _inside(region, safe_area):
                errors.append(f"[FAIL] 第{page_no}页分区【{region_name}】越出图纸有效安全区。")
            for reserved in reserved_areas:
                if _valid_box(reserved) and is_overlap(region, reserved):
                    errors.append(f"[FAIL] 第{page_no}页分区【{region_name}】进入标题栏/禁布区。")
            color = str(region.get("stroke_color", "")).lower()
            if color not in COLOR_PALETTE:
                errors.append(f"[FAIL] 第{page_no}页分区【{region_name}】描边色 {color or '未设置'} 不在随机调色板内。")

        for component in components:
            if _valid_box(component) and safe_area and _valid_box(safe_area) and not _inside(component, safe_area):
                errors.append(f"[FAIL] 第{page_no}页器件 {component.get('ref', '?')} 越出图纸安全区。")
            all_components.append(component)
            ref = str(component.get("ref", "")).strip()
            if ref:
                placed_refs.append(ref)

        if not isinstance(wires, list):
            warnings.append(f"[WARN] 第{page_no}页wires不是列表，跨区连线规则未验证。")
            wires = []
        for wire in wires:
            if wire.get("cross_region") is True or wire.get("cross_page") is True:
                method = str(wire.get("method", "")).lower()
                if method not in {"port", "flag"}:
                    errors.append(
                        f"[FAIL] 第{page_no}页跨区/跨页信号 {wire.get('net', wire.get('name', '?'))} "
                        f"没有使用Port/Flag（method={method or '未设置'}）。"
                    )

    for ref in _duplicates(placed_refs):
        errors.append(f"[FAIL] 原理图中位号重复：{ref}。")

    if "bom_refs" in project and bom_refs:
        exemptions = set(_refs(project.get("bom_exempt_refs", [])))
        bom_set, placed_set = set(bom_refs), set(placed_refs)
        missing = sorted(bom_set - placed_set)
        extra = sorted(placed_set - bom_set - exemptions)
        for ref in missing:
            errors.append(f"[FAIL] BOM位号 {ref} 在原理图中缺失。")
        for ref in extra:
            errors.append(f"[FAIL] 原理图位号 {ref} 不在BOM中，且未列入显式例外清单。")
        if exemptions:
            warnings.append(f"[WARN] 已按显式例外清单豁免位号：{', '.join(sorted(exemptions))}。请确认其为DNP/不装配或有记录的例外。")

    if not pages:
        status = "FAIL"
    else:
        status = "FAIL" if errors else "PASS"
    messages = errors if errors else ["[PASS] 工作流、分区、器件、标题、配色和BOM位号门禁通过。"]
    if any(not p.get("wires") for p in pages):
        warnings.append("[WARN] 至少一页未提供wires清单；脚本不能证明实际电气连通性，请在EDA中执行ERC/网表检查。")
    return {"status": status, "messages": messages, "warnings": warnings}


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: python scripts/check_overlap.py project.json", file=sys.stderr)
        return 2
    try:
        data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        result = check_project(data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "PASS" else 1
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "messages": [f"[FAIL] 检查器异常：{exc}"]}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
