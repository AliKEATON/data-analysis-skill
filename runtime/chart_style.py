"""图表样式与选型规则：把「配色 / 坐标轴 / 标注 / 辅助线 / 选型」规范集中为可复用逻辑。

单一事实源在 `config/defaults.yaml` 的 `charts` 节点；本模块负责加载并对外提供
确定性的规则函数，供提示词、schema 校验、渲染器和推理钩子共同使用，避免规范散落。
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_PATH = ROOT / "config" / "defaults.yaml"

_CACHE: dict | None = None


def load_style(path: Path | None = None) -> dict:
    """加载 charts 规范；不传 path 时缓存默认配置。"""
    global _CACHE
    if path is None and _CACHE is not None:
        return _CACHE
    data = yaml.safe_load((path or DEFAULTS_PATH).read_text(encoding="utf-8")) or {}
    charts = data.get("charts", {}) or {}
    if path is None:
        _CACHE = charts
    return charts


def palette(path: Path | None = None) -> dict:
    """返回规范调色板。"""
    return load_style(path).get("palette", {})


def allowed_colors(path: Path | None = None) -> set[str]:
    """返回调色板允许的颜色集合（大写十六进制）。"""
    return {str(v).upper() for v in palette(path).values() if isinstance(v, str)}


def choose_chart_type(
    category_count: int,
    has_time_series: bool = False,
    dual_axis: bool = False,
    path: Path | None = None,
) -> str:
    """按规范选择图表类型。

    时序 → line；量+率 → dual_axis；类别 ≤5 → vertical_bar；类别 ≥6 → horizontal_bar。
    """
    style = load_style(path)
    if has_time_series:
        return style.get("time_series_type", "line")
    if dual_axis:
        return "dual_axis"
    threshold = style.get("category_threshold", 5)
    if category_count <= threshold:
        return style.get("small_category_type", "vertical_bar")
    return style.get("large_category_type", "horizontal_bar")


def y_axis_max(values: list[float], factor: float | None = None, path: Path | None = None) -> float:
    """Y 轴最大值 = 数据最大绝对值 × 1.2（留标注空间）。"""
    style = load_style(path)
    factor = factor if factor is not None else style.get("axis", {}).get("max_scale_factor", 1.2)
    peak = max((abs(float(v)) for v in values), default=0.0)
    return round(peak * factor, 6)


def axis_bounds(
    values: list[float],
    from_zero: bool = True,
    factor: float | None = None,
    path: Path | None = None,
) -> dict:
    """按规范给出坐标轴范围；折线图压缩时附「非零起点」说明。"""
    style = load_style(path)
    axis_cfg = style.get("axis", {})
    numbers = [float(v) for v in values] or [0.0]
    min_value = min(numbers)
    if from_zero:
        min_value = min(min_value, 0.0)
    note = ""
    if not from_zero and axis_cfg.get("line_allow_compressed"):
        note = axis_cfg.get("compressed_note", "非零起点")
    return {
        "min": 0.0 if from_zero else round(min_value, 6),
        "max": y_axis_max(numbers, factor, path),
        "from_zero": bool(from_zero),
        "note": note,
    }


def label_style(path: Path | None = None) -> dict:
    """返回数据标注字体规范。"""
    cfg = load_style(path).get("labels", {})
    return {"fontWeight": cfg.get("font_weight", "bold"), "fontSize": cfg.get("font_size", 12)}


def select_label_points(points: list[dict], max_count: int | None = None, path: Path | None = None) -> list[dict]:
    """选出最多 3 个关键标注点：最新值、峰值、对比基准；超过阈值时关闭逐点标注。

    points 为按时间/类别顺序排列的 [{x, value}, ...]。
    """
    style = load_style(path)
    cfg = style.get("labels", {})
    limit = max_count if max_count is not None else cfg.get("max_count", 3)
    total = len(points)
    if total == 0:
        return []
    if total > cfg.get("close_when_points_over", 20):
        return []

    picks: list[dict] = []

    def add(index: int, kind: str) -> None:
        if 0 <= index < total:
            item = dict(points[index])
            item["kind"] = kind
            if item not in picks:
                picks.append(item)

    add(total - 1, "latest")
    magnitudes = [abs(float(p.get("value", 0.0))) for p in points]
    add(magnitudes.index(max(magnitudes)), "peak")
    add(0, "baseline")
    return picks[:limit]


def aux_lines(mean: float | None = None, target: float | None = None, path: Path | None = None) -> list[dict]:
    """构造辅助线声明：均值线（橙色虚线）/ 目标线（红色点线），均带标签。"""
    cfg = load_style(path).get("aux_lines", {})
    lines: list[dict] = []
    if mean is not None:
        spec = cfg.get("average", {})
        lines.append({
            "kind": "average",
            "color": spec.get("color", "#F7941D"),
            "style": spec.get("style", "dashed"),
            "value": float(mean),
            "label": f"{spec.get('label_prefix', '均值')} {float(mean):,.2f}",
        })
    if target is not None:
        spec = cfg.get("target", {})
        lines.append({
            "kind": "target",
            "color": spec.get("color", "#E63329"),
            "style": spec.get("style", "dotted"),
            "value": float(target),
            "label": f"{spec.get('label_prefix', '目标')} {float(target):,.2f}",
        })
    return lines


def build_option(
    series_values: list[float],
    palette_keys: list[str] | None = None,
    label_points: list[dict] | None = None,
    mean: float | None = None,
    target: float | None = None,
    unit: str | None = None,
    from_zero: bool = True,
    path: Path | None = None,
) -> dict:
    """按规范组装图表 option（palette / axis / labels / aux_lines / unit）。"""
    colors = palette(path)
    keys = palette_keys or ["primary_mid", "primary_light"]
    chosen = [colors[k] for k in keys if k in colors]
    return {
        "palette": chosen,
        "axis": axis_bounds(series_values, from_zero=from_zero, path=path),
        "labels": select_label_points(label_points or [], path=path),
        "aux_lines": aux_lines(mean=mean, target=target, path=path),
        "unit": unit,
    }


def validate_option(option: dict, path: Path | None = None) -> list[str]:
    """校验 option 是否符合规范，返回违规说明列表（空表示合规）。"""
    errors: list[str] = []
    colors = allowed_colors(path)
    for color in option.get("palette", []):
        if str(color).upper() not in colors:
            errors.append(f"配色 {color} 不在规范调色板内")
    max_labels = load_style(path).get("labels", {}).get("max_count", 3)
    if len(option.get("labels", [])) > max_labels:
        errors.append(f"数据标注超过 {max_labels} 个关键点")
    for line in option.get("aux_lines", []):
        if str(line.get("color", "")).upper() not in colors:
            errors.append(f"辅助线颜色 {line.get('color')} 不在规范调色板内")
    axis = option.get("axis")
    if isinstance(axis, dict) and axis.get("max") is not None:
        if axis.get("min") is not None and axis["min"] > axis["max"]:
            errors.append("坐标轴 min 不能大于 max")
    return errors
