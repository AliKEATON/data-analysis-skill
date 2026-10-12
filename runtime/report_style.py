"""报告布局与撰写规范：KPI 数量、图表剔除规则、容器尺寸、备注字体。

单一事实源在 `config/defaults.yaml` 的 `report` 节点；本模块提供确定性的规则函数，
供报告渲染器与校验逻辑复用，避免规范散落。
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_PATH = ROOT / "config" / "defaults.yaml"

_CACHE: dict | None = None


def load_report(path: Path | None = None) -> dict:
    """加载 report 规范；不传 path 时缓存默认配置。"""
    global _CACHE
    if path is None and _CACHE is not None:
        return _CACHE
    data = yaml.safe_load((path or DEFAULTS_PATH).read_text(encoding="utf-8")) or {}
    report = data.get("report", {}) or {}
    if path is None:
        _CACHE = report
    return report


def prune_charts(candidates: list[dict], path: Path | None = None) -> tuple[list[dict], list[dict]]:
    """按报告规范主动剔除图表，返回 (保留列表, 剔除列表)。

    剔除规则（满足任一即删除）：
    1. 与已保留图表同维度、同时间范围（结论重复）；
    2. 组间差异 < min_difference（无有效信息）；
    3. 加入后超过每 Tab 图表上限。

    每个候选为 dict：{id, dimension, period, values:[...], ...}；剔除项附 `reason`。
    """
    cfg = load_report(path)
    max_charts = cfg.get("max_charts_per_tab", 4)
    min_difference = cfg.get("chart_removal", {}).get("min_difference", 0.05)

    kept: list[dict] = []
    removed: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for candidate in candidates:
        key = (str(candidate.get("dimension", "")), str(candidate.get("period", "")))
        if key in seen:
            removed.append({**candidate, "reason": "与已有图表同维度、同时间范围，结论重复"})
            continue

        values = [float(v) for v in candidate.get("values", [])]
        if values:
            peak = max(abs(v) for v in values)
            spread = max(values) - min(values)
            if peak and abs(spread) / peak < min_difference:
                removed.append({
                    **candidate,
                    "reason": f"组间差异 {abs(spread) / peak:.1%} < {min_difference:.0%}，无有效信息",
                })
                continue

        seen.add(key)
        kept.append(candidate)

    if len(kept) > max_charts:
        for extra in kept[max_charts:]:
            removed.append({**extra, "reason": f"超过每 Tab 最多 {max_charts} 张图"})
        kept = kept[:max_charts]

    return kept, removed


def container_height(chart_type: str, points: int = 0, items: int = 0, path: Path | None = None) -> int | None:
    """按容器尺寸规范给出图表高度（px）；不适用时返回 None。"""
    cfg = load_report(path).get("chart_height", {})
    if chart_type == "dual_axis":
        return max(cfg.get("dual_axis", 320), cfg.get("dual_axis_min", 280))
    if chart_type in ("line", "area"):
        if points >= cfg.get("line_dense_points", 48):
            return max(cfg.get("line_dense", 300), cfg.get("line_min", 260))
        return max(cfg.get("line_base", 260), cfg.get("line_min", 260))
    if chart_type == "horizontal_bar" and items >= cfg.get("hbar_row_threshold", 7):
        return cfg.get("hbar_row", 40) * items
    return None


def note_style(kind: str, path: Path | None = None) -> dict:
    """返回备注字体规范：source / methodology / chart_conclusion。"""
    return load_report(path).get("notes", {}).get(kind, {})
