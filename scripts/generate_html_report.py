#!/usr/bin/env python3
"""Generate a self-contained management report from one completed analysis run."""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.chart_style import axis_bounds, choose_chart_type, load_style, select_label_points, y_axis_max
from runtime.report_style import container_height, load_report, prune_charts


DEFAULT_TASK = ROOT / "my_case" / "task.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "order_analysis_2026-06_07"
# 垂直柱状图的 x 轴标签悬在坐标轴下方，行内统一在图表区底部预留该高度（px），
# 避免标签溢出到「来源」文案上，同时保证同行两图的图区带高一致。
CHART_LABEL_GAP_PX = 26


def esc(value: object) -> str:
    """HTML 转义。"""
    return html.escape(str(value), quote=True)


def money(value: float) -> str:
    """金额格式化：大额用万元，否则元。"""
    value = float(value)
    if abs(value) >= 10_000:
        return f"{value / 10_000:,.2f} 万元"
    return f"{value:,.2f} 元"


def integer(value: float) -> str:
    """整数格式化：千分位。"""
    return f"{int(round(float(value))):,}"


def percent(value: float | None) -> str:
    """百分比格式化：空值返回 '不可用'。"""
    return "不可用" if value is None or pd.isna(value) else f"{float(value):.2%}"


def change(value: float | None) -> str:
    """涨跌幅格式化：带 +/-。"""
    return "不可用" if value is None or pd.isna(value) else f"{float(value):+.2%}"


def safe_ratio(numerator: float, denominator: float) -> float | None:
    """安全除法：分母为 0 返回 None。"""
    return None if not denominator else numerator / denominator


def growth(current: float, baseline: float) -> float | None:
    """增长率计算：基数 0 返回 None。"""
    return None if not baseline else current / baseline - 1


def count_csv_rows(path: Path) -> int:
    """统计 CSV 行数（不含表头）。"""
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def horizontal_bars(rows: Iterable[tuple[str, float]], value_formatter=money, row_height: int | None = None) -> str:
    """生成横向条形图 HTML；row_height 非空时按「每项 px × 数量」设置容器高度。"""
    rows = list(rows)
    if not rows:
        return '<p class="empty">暂无可展示数据</p>'
    maximum = max((abs(value) for _, value in rows), default=1) or 1
    rendered = []
    for label, value in rows:
        width = max(abs(value) / maximum * 100, 1.5)
        tone = "negative" if value < 0 else "positive"
        row_style = f' style="height:{row_height}px;margin:0"' if row_height else ""
        rendered.append(
            f'<div class="bar-row"{row_style}><div class="bar-label" title="{esc(label)}">{esc(label)}</div>'
            f'<div class="bar-track"><div class="bar-fill {tone}" style="width:{width:.3f}%"></div></div>'
            f'<div class="bar-value">{esc(value_formatter(value))}</div></div>'
        )
    container_style = f' style="min-height:{row_height * len(rows)}px"' if row_height else ""
    return f'<div class="bar-chart"{container_style}>' + "".join(rendered) + "</div>"


def vertical_bars(rows: Iterable[tuple[str, float]], value_formatter=money) -> str:
    """纵向柱状图 HTML：Y 轴从 0 开始，最大值 ×1.2 留白，类别 ≤3 时标注数值。"""
    rows = [(str(label), float(value)) for label, value in rows]
    if not rows:
        return '<p class="empty">暂无可展示数据</p>'
    peak = y_axis_max([value for _, value in rows]) or 1.0
    label_all = len(rows) <= 3
    columns = []
    for label, value in rows:
        height = max(abs(value) / peak * 150, 2)
        tone = "negative" if value < 0 else "positive"
        value_text = f'<span class="vvalue">{esc(value_formatter(value))}</span>' if label_all else ""
        columns.append(
            f'<div class="vcol">{value_text}'
            f'<div class="vbar {tone}" style="height:{height:.2f}px"></div>'
            f'<div class="vlabel" title="{esc(label)}">{esc(label)}</div></div>'
        )
    return '<div class="vbars">' + "".join(columns) + "</div>"


def _category_chart(pairs: Iterable[tuple[str, float]], value_formatter=money) -> str:
    """按类别数量自动选型：≤5 类用纵向柱，≥6 类用横向条形；并套用容器尺寸规范。"""
    rows = [(str(label), float(value)) for label, value in pairs]
    if not rows:
        return '<p class="empty">暂无可展示数据</p>'
    chart_type = choose_chart_type(len(rows))
    if chart_type == "horizontal_bar":
        total_height = container_height("horizontal_bar", items=len(rows))
        per_row = max(total_height // len(rows), 1) if total_height else None
        return horizontal_bars(rows, value_formatter, row_height=per_row)
    return vertical_bars(rows, value_formatter)


def monthly_chart(monthly: pd.DataFrame, sales: str, profit: str, sales_label: str, profit_label: str) -> str:
    """月度时序折线图（SVG）：双序列独立刻度、Y 轴最大值×1.2 留白、关键点标注≤3、叠加销售额均值线。

    不写死高度：行高由所在 `.section-row` 的图区 min-height 与 flex 拉伸决定，保证同行等高。
    """
    months = [str(m) for m in monthly["analysis_month"]]
    sales_values = [float(v) for v in monthly[sales]]
    profit_values = [float(v) for v in monthly[profit]]
    sales_scale = axis_bounds(sales_values, from_zero=True)["max"] or 1.0
    profit_scale = axis_bounds(profit_values, from_zero=True)["max"] or 1.0

    width, height_units = 720.0, 220.0
    left, right, top = 54.0, 60.0, 18.0
    plot_w = width - left - right
    plot_h = height_units - top - 30.0
    n = max(len(months), 1)

    def x_at(index: int) -> float:
        return left + (plot_w * (index / (n - 1)) if n > 1 else plot_w / 2)

    def y_at(value: float, scale: float) -> float:
        return top + plot_h - (abs(value) / scale) * plot_h

    parts = [f'<svg class="line-chart" viewBox="0 0 {width:.0f} {height_units:.0f}" preserveAspectRatio="xMidYMid meet">']
    parts.append(f'<line class="grid-line" x1="{left:.1f}" y1="{top + plot_h:.1f}" x2="{left + plot_w:.1f}" y2="{top + plot_h:.1f}"/>')

    # 销售额均值线（橙色虚线）+ 标签
    if sales_values:
        mean = sum(sales_values) / len(sales_values)
        mean_y = y_at(mean, sales_scale)
        parts.append(f'<line class="mean-line" x1="{left:.1f}" y1="{mean_y:.1f}" x2="{left + plot_w:.1f}" y2="{mean_y:.1f}"/>')
        parts.append(f'<text class="mean-label" x="{left + 4:.1f}" y="{mean_y - 4:.1f}">均值 {esc(money(mean))}</text>')

    sales_points = " ".join(f"{x_at(i):.1f},{y_at(v, sales_scale):.1f}" for i, v in enumerate(sales_values))
    profit_points = " ".join(f"{x_at(i):.1f},{y_at(v, profit_scale):.1f}" for i, v in enumerate(profit_values))
    parts.append(f'<polyline class="line-sales" points="{sales_points}"/>')
    parts.append(f'<polyline class="line-profit" points="{profit_points}"/>')

    for i, month in enumerate(months):
        parts.append(f'<text class="axis-text" x="{x_at(i):.1f}" y="{top + plot_h + 18:.1f}" text-anchor="middle">{esc(month)}</text>')

    # 关键点标注（≤3）：最新值 / 峰值 / 对比基准，仅标注销售额
    for item in select_label_points([{"x": m, "value": v} for m, v in zip(months, sales_values)]):
        try:
            index = months.index(item["x"])
        except ValueError:
            continue
        label_y = y_at(item["value"], sales_scale) - 8
        value_text = esc(f"{item['value']:,.0f}")
        parts.append(
            f'<text class="point-label-svg" x="{x_at(index):.1f}" y="{label_y:.1f}" text-anchor="middle">'
            f'{value_text}</text>'
        )
    parts.append("</svg>")

    legend = (
        '<div class="legend">'
        f'<span><i class="sales"></i>{esc(sales_label)}</span>'
        f'<span><i class="profit"></i>{esc(profit_label)}</span>'
        '<span><i class="mean"></i>均值线</span>'
        "</div>"
    )
    # 不在此处包裹 .chart-box：由章节渲染统一提供图区容器，便于同行等高。
    return "".join(parts) + legend


def _resolve_primary_from_task(task: dict, snapshot: dict) -> tuple[str, str]:
    """从 task/snapshot 推断主维度列名和显示名，保持向后兼容。"""
    output_cfg = task.get("output", {})
    dim = output_cfg.get("primary_dimension") or snapshot.get("primary_dimension") or "department"
    explicit_label = output_cfg.get("primary_entity_label")
    if explicit_label:
        label = explicit_label
    elif dim == "department":
        label = "部门"
    else:
        label = dim
    return dim, label


def _read_primary_summary(output: Path, primary_dimension: str) -> pd.DataFrame:
    """读取主维度汇总 CSV：优先读新名 dimension_{dim}.csv，不存在则回退 department_summary.csv。"""
    new_path = output / f"dimension_{primary_dimension}.csv"
    legacy_path = output / "department_summary.csv"
    if new_path.exists():
        return pd.read_csv(new_path)
    if legacy_path.exists():
        frame = pd.read_csv(legacy_path)
        # 若是 department 维度但列名是 department，正常返回
        return frame
    return pd.DataFrame()


def build_summary(task: dict, snapshot: dict, monthly: pd.DataFrame, departments: pd.DataFrame, anomaly_count: int) -> dict:
    """生成 analysis_summary 字典，同时输出新旧两套键名保持兼容。"""
    sales = snapshot["basis"]["sales"]
    profit = snapshot["basis"]["profit"]
    primary_dimension, primary_entity_label = _resolve_primary_from_task(task, snapshot)
    first, last = monthly.iloc[0], monthly.iloc[-1]
    sales_total = float(monthly[sales].sum())
    profit_total = float(monthly[profit].sum())

    neg_col_candidate = None
    for col in ["negative_calc_profit_line_count", "negative_profit_line_count"]:
        if col in monthly.columns:
            neg_col_candidate = col
            break
    negative_lines = int(monthly.get(neg_col_candidate, pd.Series(dtype=float)).sum()) if neg_col_candidate else 0

    scope_rows = int(snapshot["scope_rows"])
    eligible = departments[departments.get("line_count", 0) >= 1000] if not departments.empty else departments

    top_profit = None
    if not departments.empty and primary_dimension in departments.columns and profit in departments.columns:
        sorted_df = departments.sort_values(profit, ascending=False)
        top_profit = sorted_df.iloc[0][primary_dimension]

    risk_entity = None
    if not eligible.empty:
        rate_col = None
        for rc in ["negative_calc_profit_line_rate", "negative_profit_line_rate"]:
            if rc in eligible.columns:
                rate_col = rc
                break
        if rate_col:
            sorted_rate = eligible.sort_values(rate_col, ascending=False)
            risk_entity = sorted_rate.iloc[0][primary_dimension]
        elif neg_col_candidate and neg_col_candidate in eligible.columns:
            sorted_cnt = eligible.sort_values(neg_col_candidate, ascending=False)
            risk_entity = sorted_cnt.iloc[0][primary_dimension]

    comparison = {
        "baseline": str(first["analysis_month"]),
        "target": str(last["analysis_month"]),
        "sales_growth": growth(float(last[sales]), float(first[sales])),
        "order_growth": growth(float(last["order_count"]), float(first["order_count"])),
        "profit_growth": growth(float(last[profit]), float(first[profit])),
        "margin_change_pp": (safe_ratio(float(last[profit]), float(last[sales])) or 0) * 100
        - (safe_ratio(float(first[profit]), float(first[sales])) or 0) * 100,
    }

    basis_unit = snapshot["basis"].get("sales_unit") or ""
    profit_spec = task.get("metrics", [])
    profit_spec = next((m for m in profit_spec if m.get("role") == "profit"), {})
    profit_def = profit_spec.get("label") or f"{primary_entity_label}利润"
    summary = {
        "basis": {"sales": sales, "sales_unit": basis_unit, "profit": profit, "profit_definition": profit_def},
        "analysis_period": snapshot["analysis_period"],
        "scope_rows": scope_rows,
        "orders": int(monthly["order_count"].sum()),
        "sales_amount": sales_total,
        "pricing_profit": profit_total,
        "profit_margin": safe_ratio(profit_total, sales_total),
        "negative_pricing_profit_lines": negative_lines,
        "negative_pricing_profit_line_rate": safe_ratio(negative_lines, scope_rows),
        "anomaly_rows": anomaly_count,
        "period_comparison": comparison,
        "top_department_by_pricing_profit": top_profit,
        "highest_negative_pricing_profit_rate_department_over_1000_lines": risk_entity,
        "highest_negative_profit_rate_primary_entity_over_1000_lines": risk_entity,
        f"top_{primary_entity_label}_by_profit": top_profit,
    }
    # 保留旧键名用于向后兼容（供黄金夹具测试使用）
    summary.update({
        "amt_rmb": sales_total,
        "calc_final_profit": profit_total,
        "calc_profit_margin": summary["profit_margin"],
        "negative_calc_profit_lines": negative_lines,
        "negative_calc_profit_line_rate": summary["negative_pricing_profit_line_rate"],
        "top_department_by_calc_final_profit": top_profit,
        "highest_negative_calc_profit_rate_department_over_1000_lines": risk_entity,
    })
    return summary


def _build_basis_span(sales_label: str, profit_label: str, order_field: str, sales_field: str, profit_field: str, unit: str) -> str:
    """动态构建 <div class="basis"> 内的文案。"""
    unit_text = unit if unit and unit not in {"CNY", "人民币", "元", ""} else "人民币"
    sales_text = f"<b>{sales_label}：{sales_field}（{unit_text}）</b>"
    profit_text = f"<b>{profit_label}：{profit_field}</b>"
    order_text = f"<b>订单：</b>{order_field} 去重"
    return f"<span>{sales_text}</span><span>{profit_text}</span><span>{order_text}</span>"


def _df_to_markdown_table(df: pd.DataFrame, limit: int = 10) -> str:
    """将 DataFrame 转为 Markdown 表格（无第三方依赖）。"""
    if df.empty:
        return ""
    headers = [str(h) for h in df.columns]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for _, row in df.head(limit).iterrows():
        cells = [str(row[h]) if pd.notna(row[h]) else "" for h in df.columns]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _render_markdown(
    report_title: str,
    report_subtitle: str,
    audience: str,
    start: str,
    end: str,
    summary: dict,
    comparison: dict,
    sales_label: str,
    profit_label: str,
    margin_label: str,
    order_label: str,
    monthly: pd.DataFrame,
    departments: pd.DataFrame,
    primary_entity_label: str,
    anomaly_count: int,
    generated_at: str,
    synthesis: dict | None = None,
    section_specs: list[dict] | None = None,
) -> str:
    """生成 Markdown 格式的经营分析报告。

    摘要（结论摘要）与 HTML 分支共用 `_conclusion_index`：只给一句话结论 + 本次问题清单；
    各问题的现象/定位/解释/建议动作改到正文对应问题章节中呈现一次，不再在摘要重复。
    """
    lines = [
        f"# {report_title}",
        "",
        f"*{report_subtitle}*",
        "",
        f"- 分析期间：{start} 至 {end}",
        f"- 面向：{audience}",
        f"- 生成时间：{generated_at}",
        "",
    ]
    index = _conclusion_index(synthesis, section_specs)
    if index["headline"] or index["titles"]:
        lines += ["## 结论摘要", ""]
        if index["headline"]:
            lines += [f"**{index['headline']}**", ""]
        if index["titles"]:
            lines += [f"本次共 {len(index['titles'])} 个问题：", ""]
            lines += [f"{item['index']}. {item['title']}" for item in index["titles"]]
            lines += [""]
    lines += [
        "## 经营概览",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| {sales_label} | {money(summary['sales_amount'])} |",
        f"| {profit_label} | {money(summary['pricing_profit'])} |",
        f"| {margin_label} | {percent(summary['profit_margin'])} |",
        f"| {order_label} | {integer(summary['orders'])} |",
        f"| 分析明细行数 | {integer(summary['scope_rows'])} |",
        "",
        "## 主要结论",
        "",
        f"- {comparison['target']} 较 {comparison['baseline']}：{sales_label} {change(comparison['sales_growth'])}，{profit_label} {change(comparison['profit_growth'])}。",
        f"- 负{profit_label}明细 {integer(summary['negative_pricing_profit_lines'])} 行，占比 {percent(summary['negative_pricing_profit_line_rate'])}。",
        f"- 异常清单共 {integer(anomaly_count)} 行。",
        "",
    ]
    # 正文问题章节：与 HTML 共用同一批 section_specs，结构与顺序一致（标题→叙述→来源→结论）；
    # 同一问题模块的结论卡片只在其最后一张卡片处呈现一次（与 HTML 分支同一规则）。
    for spec in section_specs or []:
        heading_index = spec.get("index")
        heading = f"## {heading_index}. {spec.get('title', '')}" if heading_index else f"## {spec.get('title', '')}"
        lines += [heading, ""]
        if spec.get("narrative"):
            lines += [spec["narrative"], ""]
        if spec.get("source"):
            lines += [f"来源：{spec['source']}", ""]
        card_lines = _global_card_markdown(spec.get("global")) if spec.get("module_last", True) else []
        if card_lines:
            lines += card_lines + [""]
    if not monthly.empty:
        lines.append("## 月度表现")
        lines.append("")
        lines.append(_df_to_markdown_table(monthly))
        lines.append("")
    if not departments.empty:
        lines.append(f"## {primary_entity_label}表现（前 10）")
        lines.append("")
        lines.append(_df_to_markdown_table(departments))
        lines.append("")
    return "\n".join(lines) + "\n"


def _render_xlsx(output: Path, summary: dict, monthly: pd.DataFrame, departments: pd.DataFrame, anomaly_count: int) -> Path:
    """生成 XLSX 多 sheet 报告，返回写入的文件路径。"""
    xlsx_path = output / "management_report.xlsx"
    overview = pd.DataFrame([
        {"指标": "销售额", "数值": summary.get("sales_amount")},
        {"指标": "计价利润", "数值": summary.get("pricing_profit")},
        {"指标": "利润率", "数值": summary.get("profit_margin")},
        {"指标": "订单数", "数值": summary.get("orders")},
        {"指标": "分析明细行数", "数值": summary.get("scope_rows")},
        {"指标": "异常行数", "数值": anomaly_count},
    ])
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        overview.to_excel(writer, sheet_name="概览", index=False)
        if not monthly.empty:
            monthly.to_excel(writer, sheet_name="月度汇总", index=False)
        if not departments.empty:
            departments.to_excel(writer, sheet_name="主实体汇总", index=False)
        anomaly_csv = output / "anomaly_orders.csv"
        if anomaly_csv.exists():
            anomaly = pd.read_csv(anomaly_csv, encoding="utf-8-sig")
            if not anomaly.empty:
                anomaly.to_excel(writer, sheet_name="异常明细", index=False)
    return xlsx_path


def _load_context(task_path: Path, output: Path) -> dict:
    """加载渲染所需的全部上下文：任务、执行快照、汇总数据与指标标签。"""
    task = json.loads(task_path.read_text(encoding="utf-8"))
    snapshot = json.loads((output / "execution_snapshot.json").read_text(encoding="utf-8"))
    monthly = pd.read_csv(output / "monthly_summary.csv").sort_values("analysis_month")

    primary_dimension, primary_entity_label = _resolve_primary_from_task(task, snapshot)
    departments = _read_primary_summary(output, primary_dimension)
    # 若主维度列在 departments 中但列名不同（旧产物 department_summary.csv 里列是 department），重命名对齐
    if primary_dimension != "department" and "department" in departments.columns and primary_dimension not in departments.columns:
        departments = departments.rename(columns={"department": primary_dimension})

    sales, profit = snapshot["basis"]["sales"], snapshot["basis"]["profit"]
    output_config = task.get("output", {})
    metric_specs = {metric["name"]: metric for metric in task.get("metrics", [])}
    sales_spec = metric_specs.get(sales, {})
    profit_spec = metric_specs.get(profit, {})
    sales_label = sales_spec.get("label") or "销售额"
    profit_label = profit_spec.get("label") or "利润"
    order_spec = metric_specs.get("order_count", {}) or next(
        (m for m in task.get("metrics", []) if m.get("role") == "order_count"), {}
    )
    order_label = order_spec.get("label") or "去重订单数"
    margin_spec = next((m for m in task.get("metrics", []) if m.get("role") == "margin"), {})
    margin_label = margin_spec.get("label") or f"{profit_label}率"
    order_field = order_spec.get("source_field", "order_number")
    report_title = output_config.get("title") or task.get("analysis_goal", "数据分析报告").split("，", 1)[0]
    report_subtitle = output_config.get("subtitle") or "经营分析 · 自动生成"
    audience = output_config.get("audience") or "业务使用者"

    return {
        "task": task,
        "snapshot": snapshot,
        "monthly": monthly,
        "departments": departments,
        "primary_dimension": primary_dimension,
        "primary_entity_label": primary_entity_label,
        "sales": sales,
        "profit": profit,
        "sales_spec": sales_spec,
        "profit_spec": profit_spec,
        "sales_label": sales_label,
        "profit_label": profit_label,
        "order_label": order_label,
        "margin_label": margin_label,
        "order_field": order_field,
        "report_title": report_title,
        "report_subtitle": report_subtitle,
        "audience": audience,
        "output_config": output_config,
        "anomaly_count": count_csv_rows(output / "anomaly_orders.csv"),
    }


def build_analysis_summary(task_path: Path, output: Path) -> dict:
    """构建并写出 analysis_summary.json（须在 build_run 之前调用，供其读取口径元数据）。"""
    ctx = _load_context(task_path, output)
    summary = build_summary(ctx["task"], ctx["snapshot"], ctx["monthly"], ctx["departments"], ctx["anomaly_count"])
    (output / "analysis_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _conclusion_index(synthesis: dict | None, section_specs: list[dict] | None) -> dict:
    """构造「结论摘要」的极简索引：一句话结论 + 本次问题标题清单。

    HTML 与 Markdown 两条渲染分支共用本函数，保证摘要结构一致、不分叉；
    现象/定位/解释/建议动作只在正文各问题的结论卡片出现一次。
    """
    headline = ((synthesis or {}).get("headline") or {}).get("text", "")
    specs = section_specs or []
    titles = [
        {
            "index": spec.get("index", position),
            "title": spec.get("title") or f"问题 {position}",
        }
        for position, spec in enumerate(specs, start=1)
    ]
    return {"headline": headline, "titles": titles}


def _render_conclusions_html(synthesis: dict | None, section_specs: list[dict] | None = None) -> str:
    """渲染 L1 结论摘要：仅「一句话结论 + 本次问题清单（带正文锚点）」。

    不再重复各洞察的现象/定位/解释/建议动作——这些只在正文图表结论卡片出现一次；
    清单项链接到正文 `<article id="qN">` 锚点。
    """
    index = _conclusion_index(synthesis, section_specs)
    blocks: list[str] = []
    if index["headline"]:
        blocks.append(f'<p class="headline">{esc(index["headline"])}</p>')
    if index["titles"]:
        items = "".join(
            f'<li><a href="#q{item["index"]}">{esc(item["title"])}</a></li>' for item in index["titles"]
        )
        blocks.append(
            f'<p class="tab-note">本次共 {len(index["titles"])} 个问题，点击可跳转至对应章节：</p>'
            f'<ol class="question-index">{items}</ol>'
        )
    if not blocks:
        return ""
    return '<section><h2>结论摘要</h2><div class="conclusions">' + "".join(blocks) + "</div></section>"


def _global_card_markdown(gi: dict | None) -> list[str]:
    """把全局洞察渲染为 Markdown 行，字段与 HTML 图表结论卡片保持一致。"""
    if not gi:
        return []
    lines: list[str] = []
    body = " ".join(
        part for part in [gi.get("phenomenon", ""), gi.get("localization", ""), gi.get("explanation", "")] if part
    )
    if body:
        lines.append(body)
    action = gi.get("action") or {}
    action_text = " · ".join(
        part for part in [action.get("owner", ""), action.get("timing", ""), action.get("what", "")] if part
    )
    if action_text:
        lines.append(
            f"建议动作：{action_text}（预期影响：{action.get('expected_impact', '')}；"
            f"验证：{action.get('verification', '')}）"
        )
    return lines


def _module_key(spec: dict) -> str:
    """问题模块标识：优先取该章节关联的全局洞察 id（同一洞察=同一问题模块），回退到图表 id / 序号。"""
    insight = spec.get("global") or {}
    return str(insight.get("id") or spec.get("chart_id") or spec.get("index"))


def _section_columns_config() -> tuple[int, str]:
    """读取每屏最大列数与列比例：`charts.max_columns_per_screen` + `report.chart_columns`。"""
    max_columns = int(load_style().get("max_columns_per_screen", 2) or 2)
    ratio = str(load_report().get("chart_columns") or "")
    return max(max_columns, 1), ratio


def _grid_template_columns(columns: int, ratio_text: str) -> str:
    """把「列数 + 比例」解析为 grid-template-columns；比例缺失或非等分不可解析时等分。"""
    if columns <= 1:
        return "minmax(0,1fr)"
    parts = [part.strip() for part in ratio_text.split(":") if part.strip()]
    if len(parts) == columns:
        try:
            weights = [float(part) for part in parts]
        except ValueError:
            weights = []
        if weights and all(weight > 0 for weight in weights) and len(set(weights)) > 1:
            return " ".join(f"{weight:g}fr" for weight in weights)
    return f"repeat({columns},minmax(0,1fr))"


def _row_chart_height(row_specs: list[dict]) -> int:
    """按行取各图基准高度的较大值，作为该行图表本体的高度（不做全局拉平）。

    高度基准来自 `report.chart_height`（折线 260/300、横向条形每项 40px 等）；
    无可用基准时回退 240px。行内两图使用同一数值，因此图表可视区必然等高。
    """
    heights: list[int] = []
    for spec in row_specs:
        values = spec.get("values") or []
        height = container_height(
            str(spec.get("chart_type") or "vertical_bar"), points=len(values), items=len(values)
        )
        if height:
            heights.append(int(height))
    return max(heights) if heights else 240


def render_report(task_path: Path, output: Path) -> dict:
    """渲染管理报告（html/markdown/xlsx），读取 run/synthesis.json 以纳入 L1 结论页。

    应在 build_run 之后调用，这样报告能引用本地 Agent 产出的全局洞察与建议动作。
    """
    ctx = _load_context(task_path, output)
    task, snapshot = ctx["task"], ctx["snapshot"]
    monthly, departments = ctx["monthly"], ctx["departments"]
    primary_dimension, primary_entity_label = ctx["primary_dimension"], ctx["primary_entity_label"]
    sales, profit = ctx["sales"], ctx["profit"]
    sales_label, profit_label = ctx["sales_label"], ctx["profit_label"]
    margin_label, order_label = ctx["margin_label"], ctx["order_label"]
    order_field = ctx["order_field"]
    report_title, report_subtitle = ctx["report_title"], ctx["report_subtitle"]
    audience = ctx["audience"]
    output_config = ctx["output_config"]
    anomaly_count = ctx["anomaly_count"]
    sales_spec = ctx["sales_spec"]

    summary_path = output / "analysis_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    else:
        summary = build_summary(task, snapshot, monthly, departments, anomaly_count)

    synthesis = None
    synthesis_path = output / "run" / "synthesis.json"
    if synthesis_path.exists():
        synthesis = json.loads(synthesis_path.read_text(encoding="utf-8"))

    start = datetime.fromisoformat(snapshot["analysis_period"]["start"]).strftime("%Y年%m月%d日")
    end_exclusive = datetime.fromisoformat(snapshot["analysis_period"]["end_exclusive"])
    end = (end_exclusive - pd.Timedelta(days=1)).strftime("%Y年%m月%d日")
    comparison = summary["period_comparison"]
    period_label = f"{comparison.get('baseline', '')}至{comparison.get('target', '')}"
    margin_delta = comparison["margin_change_pp"]
    direction = "提升" if margin_delta >= 0 else "下降"
    generated_at = datetime.fromisoformat(snapshot["generated_at"]).astimezone().strftime("%Y-%m-%d %H:%M")

    basis_unit = snapshot["basis"].get("sales_unit") or sales_spec.get("unit") or ""
    report_unit_label = basis_unit if basis_unit and basis_unit not in {"CNY", ""} else "人民币"

    # ---- 问题驱动：报告主体由 Agent 在 run/charting.json 声明的图表逐条生成 ----
    # 结构与样式固定（标题→问题叙述→证据图表→来源→结论卡片）；板块内容与数量由声明决定，不套用固定模板。
    dim_labels = output_config.get("dimension_labels", {}) or {}

    insight_text: dict[str, str] = {}
    local_insight_path = output / "run" / "local_insight.json"
    if local_insight_path.exists():
        for item in json.loads(local_insight_path.read_text(encoding="utf-8")).get("insights", []):
            insight_text[item.get("id", "")] = item.get("text", "")

    # 全局洞察按被引用的图表建立索引，供「结论卡片」使用
    global_by_chart: dict[str, dict] = {}
    if synthesis:
        for gi in synthesis.get("global_insights", []):
            for ref in gi.get("evidence_refs", []):
                global_by_chart.setdefault(str(ref), gi)

    charting_path = output / "run" / "charting.json"
    declared_charts: list[dict] = []
    if charting_path.exists():
        declared_charts = json.loads(charting_path.read_text(encoding="utf-8")).get("charts", [])
    selected_ids = set((synthesis or {}).get("selected_chart_ids") or [])
    ordered_charts = [c for c in declared_charts if not selected_ids or c.get("id") in selected_ids]

    def _chart_render(chart: dict) -> tuple[list[float], str, list[tuple[str, float]] | None]:
        """按图表声明取数并渲染为 HTML，返回 (数值序列, HTML, 类别明细或 None)。"""
        x = str((chart.get("encoding") or {}).get("x", ""))
        y = (chart.get("encoding") or {}).get("y")
        if x == "analysis_month":
            return [float(v) for v in monthly[sales]], monthly_chart(monthly, sales, profit, sales_label, profit_label), None
        metric = y[0] if isinstance(y, list) and y else (y if isinstance(y, str) else profit)
        path = output / f"dimension_{x}.csv"
        if not path.exists():
            return [], '<p class="empty">暂无可展示数据</p>', None
        frame = pd.read_csv(path)
        if x not in frame.columns or metric not in frame.columns:
            return [], '<p class="empty">暂无可展示数据</p>', None
        frame[x] = frame[x].fillna("<空>").replace("", "<空>")
        top = frame.sort_values(metric, ascending=False).head(8)
        pairs = [(str(row[x]), float(row[metric])) for _, row in top.iterrows()]
        return [value for _, value in pairs], _category_chart(pairs), pairs

    chart_candidates = []
    for chart in ordered_charts:
        values, chart_html, pairs = _chart_render(chart)
        chart_candidates.append({
            "id": chart.get("id", ""),
            "dimension": str((chart.get("encoding") or {}).get("x", "")),
            "period": period_label,
            "values": values,
            "chart": chart,
            "html": chart_html,
            "pairs": pairs,
        })
    kept_charts, removed_charts = prune_charts(chart_candidates)

    # 章节规格：HTML 与 Markdown 共用同一批章节数据（index 与正文 #qN 锚点一一对应）
    section_specs: list[dict] = []
    for position, candidate in enumerate(kept_charts, start=1):
        dimension = candidate["dimension"]
        display = dim_labels.get(dimension, "月度趋势" if dimension == "analysis_month" else dimension)
        section_specs.append({
            "index": position,
            "chart_id": candidate["id"],
            "title": candidate["chart"].get("title") or display,
            "dimension": dimension,
            "chart_type": str(candidate["chart"].get("chart_type") or ""),
            "narrative": insight_text.get(candidate["chart"].get("insight_id", ""), ""),
            "source": f"{display} 汇总（{period_label}）",
            "html": candidate["html"],
            "pairs": candidate["pairs"],
            "values": candidate["values"],
            "global": global_by_chart.get(candidate["id"]),
        })
    # 同一问题模块的图表共用一条结论卡片，只在「该模块最后一张卡片」里呈现一次，避免重复。
    module_last_index: dict[str, int] = {}
    for spec in section_specs:
        module_last_index[_module_key(spec)] = spec["index"]
    for spec in section_specs:
        spec["module_last"] = spec["index"] == module_last_index[_module_key(spec)]

    prune_note = ""
    if removed_charts:
        reasons = "；".join(f"{item['id']}（{item['reason']}）" for item in removed_charts)
        prune_note = f'<p class="tab-note">已按规范剔除 {len(removed_charts)} 张图：{esc(reasons)}</p>'

    # 按 output.format 分发到不同渲染器；markdown/xlsx 走独立分支，html 走下方原有逻辑。
    output_format = output_config.get("format", "html")
    if output_format == "markdown":
        markdown_text = _render_markdown(
            report_title, report_subtitle, audience, start, end,
            summary, comparison, sales_label, profit_label, margin_label, order_label,
            monthly, departments, primary_entity_label, anomaly_count, generated_at,
            synthesis=synthesis,
            section_specs=section_specs,
        )
        report_path = output / "management_report.md"
        report_path.write_text(markdown_text, encoding="utf-8")
        return {"report": str(report_path), "summary": str(output / "analysis_summary.json"), "anomaly_rows": anomaly_count}
    if output_format == "xlsx":
        report_path = _render_xlsx(output, summary, monthly, departments, anomaly_count)
        return {"report": str(report_path), "summary": str(output / "analysis_summary.json"), "anomaly_rows": anomaly_count}

    basis_html = _build_basis_span(sales_label, profit_label, order_field, sales, profit, basis_unit)

    dept_view = pd.DataFrame()
    if not departments.empty and primary_dimension in departments.columns and profit in departments.columns:
        dept_view = departments.sort_values(profit, ascending=False).head(12)
    dim_key_col = primary_dimension if primary_dimension in departments.columns else "department"

    def _dept_rows() -> str:
        if dept_view.empty or dim_key_col not in dept_view.columns:
            return ""
        rows = []
        for _, row in dept_view.iterrows():
            pval = row.get(dim_key_col, "")
            sval = row.get(sales, 0.0)
            pval_money = row.get(profit, 0.0)
            marg = safe_ratio(float(pval_money), float(sval)) if sval else None
            ords = row.get("order_count", 0)
            neg_rate = None
            for rc in ["negative_calc_profit_line_rate", "negative_profit_line_rate"]:
                if rc in row.index:
                    neg_rate = row[rc]
                    break
            rows.append(
                f"<tr><td>{esc(pval)}</td><td>{money(sval)}</td><td>{money(pval_money)}</td>"
                f"<td>{percent(marg)}</td><td>{integer(ords)}</td>"
                f"<td>{percent(neg_rate)}</td></tr>"
            )
        return "".join(rows)

    dept_rows = _dept_rows()

    def _global_card(gi: dict | None) -> str:
        """图表结论卡片：关联全局洞察（现象/定位/解释 + 建议动作），12px / #444 / 蓝色左边框。"""
        if not gi:
            return ""
        body = " ".join(part for part in [gi.get("phenomenon", ""), gi.get("localization", ""), gi.get("explanation", "")] if part)
        action = gi.get("action") or {}
        action_text = " · ".join(part for part in [action.get("owner", ""), action.get("timing", ""), action.get("what", "")] if part)
        cards = f'<div class="chart-conclusion">{esc(body)}</div>' if body else ""
        if action_text:
            cards += f'<div class="chart-conclusion">建议动作：{esc(action_text)}（预期影响：{esc(action.get("expected_impact", ""))}；验证：{esc(action.get("verification", ""))}）</div>'
        return cards

    # 每个被保留的图表 → 一张「问题」卡片：标题 → 问题叙述 → 证据图表 → 来源 → 结论卡片。
    # 正文按「问题模块」成行：**同一问题模块独占一行**（不同模块不得挤在同一行），
    # 只有同属该模块的证据图表才在该行内并排（最多 max_columns 张、同行等高）；
    # 时序图（analysis_month）宽度需求大，模块内也独占一行。
    max_columns, ratio_text = _section_columns_config()
    rows: list[list[dict]] = []
    module_buckets: dict[str, list[list[dict]]] = {}
    for spec in section_specs:
        is_wide = spec.get("dimension") == "analysis_month"
        buckets = module_buckets.setdefault(_module_key(spec), [])
        if (
            is_wide
            or not buckets
            or len(buckets[-1]) >= max_columns
            or buckets[-1][0].get("dimension") == "analysis_month"
        ):
            buckets.append([])
            rows.append(buckets[-1])
        buckets[-1].append(spec)

    sections_html = ""
    for row in rows:
        cards = []
        for spec in row:
            conclusion = _global_card(spec["global"]) if spec.get("module_last") else ""
            cards.append(
                f'<article class="question-card" id="q{spec["index"]}">'
                f'<h2>{esc(spec["title"])}</h2>'
                + (f'<p class="question-narrative">{esc(spec["narrative"])}</p>' if spec["narrative"] else "")
                + f'<div class="chart-box">{spec["html"]}</div>'
                + f'<p class="chart-source">来源：{esc(spec["source"])}</p>'
                + conclusion
                + "</article>"
            )
        row_class = "section-row single" if len(row) == 1 else "section-row"
        row_height = _row_chart_height(row) + CHART_LABEL_GAP_PX
        sections_html += (
            f'<div class="{row_class}" style="--chart-height:{row_height}px">'
            + "".join(cards)
            + "</div>"
        )

    monthly_lines = int(monthly["line_count"].sum()) if "line_count" in monthly.columns else 0
    department_lines = (
        int(departments["line_count"].sum())
        if not departments.empty and "line_count" in departments.columns
        else 0
    )
    quality_rows = [
        f"<tr><td>pass</td><td>scope_rows</td><td>本次期间内明细为 {integer(snapshot['scope_rows'])} 行</td><td>与执行快照一致</td></tr>",
        f"<tr><td>{'pass' if monthly_lines == snapshot['scope_rows'] else 'warning'}</td><td>monthly_summary</td><td>月度汇总勾稽 {integer(monthly_lines)} 行</td><td>应等于期间内明细行数</td></tr>",
        f"<tr><td>{'pass' if department_lines == snapshot['scope_rows'] else 'warning'}</td><td>{dim_key_col}_summary</td><td>{primary_entity_label}汇总勾稽 {integer(department_lines)} 行</td><td>应等于期间内明细行数</td></tr>",
        f"<tr><td>pass</td><td>metric_basis</td><td>{sales} {sales_label}；{profit} {profit_label}</td><td>按 task.metrics 角色动态注入</td></tr>",
    ]
    quality_path = output / "data_quality_issues.csv"
    snapshot_path = output / "execution_snapshot.json"
    if quality_path.exists() and quality_path.stat().st_mtime >= snapshot_path.stat().st_mtime:
        quality = pd.read_csv(quality_path)
        quality = quality[~quality["field"].astype(str).str.contains("final_profit", case=False, na=False)]
        for _, row in quality.head(8).iterrows():
            quality_rows.append(
                f"<tr><td>{esc(row['severity'])}</td><td>{esc(row['field'])}</td><td>{esc(row['description'])}</td><td>{esc(row['recommendation'])}</td></tr>"
            )

    # 5 个 KPI 卡片：最新期主指标、环比、利润率、TOP 主实体、TOP 次维度。
    latest_sales = float(monthly[sales].iloc[-1])
    latest_month = str(monthly["analysis_month"].iloc[-1])
    top_primary = summary.get(f"top_{primary_entity_label}_by_profit") or summary.get("top_department_by_pricing_profit") or "—"
    secondary_dims = [d for d in task.get("dimensions", []) if d != primary_dimension]
    secondary_label = dim_labels.get(secondary_dims[0], secondary_dims[0]) if secondary_dims else "次维度"
    top_secondary = "—"
    if secondary_dims:
        sec_path = output / f"dimension_{secondary_dims[0]}.csv"
        if sec_path.exists():
            sec_frame = pd.read_csv(sec_path)
            sec_col = secondary_dims[0]
            if sec_col in sec_frame.columns and profit in sec_frame.columns:
                sec_frame[sec_col] = sec_frame[sec_col].fillna("<空>").replace("", "<空>")
                top_secondary = str(sec_frame.sort_values(profit, ascending=False).iloc[0][sec_col])
    primary_count = len(departments) if not departments.empty else 0
    kpi_html = (
        f'<div class="kpi"><span>最新期{esc(sales_label)}（{esc(latest_month)}）</span><strong>{money(latest_sales)}</strong>'
        f'<small>{esc(report_unit_label)} · 字段 {esc(sales)}</small></div>'
        f'<div class="kpi"><span>环比（{esc(str(comparison.get("target", "")))} vs {esc(str(comparison.get("baseline", "")))}）</span>'
        f'<strong>{change(comparison.get("sales_growth"))}</strong><small>订单数 {change(comparison.get("order_growth"))}</small></div>'
        f'<div class="kpi"><span>{esc(margin_label)}</span><strong>{percent(summary["profit_margin"])}</strong>'
        f'<small>{esc(profit_label)} / {esc(sales_label)}</small></div>'
        f'<div class="kpi"><span>TOP{esc(primary_entity_label)}（按{esc(profit_label)}）</span><strong>{esc(str(top_primary))}</strong>'
        f'<small>共 {primary_count} 个{esc(primary_entity_label)}</small></div>'
        f'<div class="kpi"><span>TOP{esc(secondary_label)}（按{esc(profit_label)}）</span><strong>{esc(str(top_secondary))}</strong>'
        f'<small>次维度最高</small></div>'
    )

    tab_note = f'<p class="tab-note">口径：{esc(sales_label)}={esc(sales)}，{esc(profit_label)}={esc(profit)}；{esc(order_label)}按 {esc(order_field)} 去重；金额单位 {esc(report_unit_label)}。</p>'

    conclusions_html = _render_conclusions_html(synthesis, section_specs)
    row_columns = _grid_template_columns(max_columns, ratio_text)
    report = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(report_title)}</title><style>
:root{{--ink:#17202a;--muted:#64707d;--line:#dce2e7;--paper:#fff;--soft:#f4f7f8;--primary:#0073CF;--primary-dark:#003366;--primary-light:#5AB4E0;--positive:#00A551;--negative:#E63329;--average:#F7941D}}
*{{box-sizing:border-box}}html,body{{max-width:100%;overflow-x:hidden}}body{{margin:0;background:#eef2f3;color:var(--ink);font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif;line-height:1.55;letter-spacing:0}}
.page{{width:100%;max-width:1180px;margin:auto;background:var(--paper);min-height:100vh}}header{{padding:48px 56px 36px;border-top:8px solid var(--primary-dark);border-bottom:1px solid var(--line)}}
.eyebrow{{color:var(--primary);font-size:13px;font-weight:700}}h1{{font-size:34px;margin:6px 0 10px}}h2{{font-size:22px;margin:0 0 20px}}h3{{font-size:16px;margin:0 0 14px}}.subtitle,.muted{{color:var(--muted)}}
main section{{padding:34px 56px;border-bottom:1px solid var(--line)}}main>.section-row{{padding:34px 56px;border-bottom:1px solid var(--line)}}.basis{{display:flex;gap:24px;flex-wrap:wrap;margin-top:20px;padding:14px 18px;background:var(--soft);border-left:4px solid var(--primary);font-size:14px}}.basis span{{min-width:0;overflow-wrap:anywhere}}
.kpis{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line)}}.kpi{{min-width:0;background:white;padding:20px;overflow-wrap:anywhere}}.kpi span{{display:block;color:var(--muted);font-size:13px}}.kpi strong{{display:block;font-size:24px;margin-top:5px;white-space:nowrap}}.kpi small{{color:var(--muted)}}
.findings{{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}}.finding{{border-top:3px solid var(--primary);padding:16px 0}}.finding strong{{font-size:20px;display:block;margin:5px 0}}
.two-col{{display:grid;grid-template-columns:1.5fr 1fr;gap:36px;align-items:start}}.chart-box{{position:relative;padding:8px 4px 0}}svg.line-chart{{width:100%;height:260px;display:block;overflow:visible}}.grid-line{{stroke:var(--line);stroke-width:1}}.axis-text{{fill:var(--muted);font-size:11px}}.line-sales{{fill:none;stroke:var(--primary);stroke-width:2.5}}.line-profit{{fill:none;stroke:var(--primary-light);stroke-width:2.5}}.dot-sales{{fill:var(--primary)}}.dot-profit{{fill:var(--primary-light)}}.mean-line{{stroke:var(--average);stroke-width:1.6;stroke-dasharray:6 4}}.mean-label{{fill:var(--average);font-size:12px;font-weight:bold}}.point-label-svg{{fill:var(--primary-dark);font-weight:bold;font-size:12px}}.legend{{display:flex;gap:20px;justify-content:center;margin-top:10px;font-size:12px;color:var(--muted)}}.legend i{{display:inline-block;width:10px;height:10px;margin-right:5px}}.legend i.sales{{background:var(--primary)}}.legend i.profit{{background:var(--primary-light)}}.legend i.mean{{background:var(--average)}}.vbars{{display:flex;align-items:flex-end;gap:18px;height:190px;border-bottom:1px solid var(--line);padding:0 8px}}.vcol{{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:100%;position:relative}}.vbar{{width:60%;max-width:54px;min-height:2px;border-radius:2px 2px 0 0}}.vbar.positive{{background:var(--primary)}}.vbar.negative{{background:var(--negative)}}.vlabel{{position:absolute;bottom:-22px;font-size:11px;color:var(--muted);text-align:center;width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.vvalue{{font-weight:bold;font-size:12px;color:var(--primary-dark);margin-bottom:3px}}
.bar-row{{display:grid;grid-template-columns:minmax(120px,1.3fr) 2fr 110px;gap:10px;align-items:center;margin:9px 0;font-size:12px}}.bar-label{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.bar-track{{height:10px;background:var(--soft)}}.bar-fill{{height:100%}}.bar-fill.positive{{background:var(--primary)}}.bar-fill.negative{{background:var(--negative)}}.bar-value{{text-align:right;font-variant-numeric:tabular-nums}}
.vbars{{margin-bottom:26px}}
.table-wrap{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:13px}}th{{text-align:left;color:var(--muted);font-weight:600;background:var(--soft)}}th,td{{padding:10px 12px;border-bottom:1px solid var(--line)}}td:not(:first-child){{font-variant-numeric:tabular-nums}}
.dimensions{{display:grid;grid-template-columns:repeat(2,1fr);gap:32px}}.dimension{{min-width:0}}.method{{display:grid;grid-template-columns:1fr 1fr;gap:28px}}.method>article{{min-width:0}}code{{overflow-wrap:anywhere;word-break:break-all}}ul{{padding-left:20px}}footer{{padding:24px 56px;color:var(--muted);font-size:12px}}
.headline{{font-size:18px;font-weight:600;margin:0 0 16px}}.question-index{{margin:8px 0 0;padding-left:22px}}.question-index li{{margin:5px 0}}.question-index a{{color:var(--primary);text-decoration:none}}.question-index a:hover{{text-decoration:underline}}
.section-row{{display:grid;grid-template-columns:{row_columns};gap:32px;align-items:stretch}}.section-row.single{{grid-template-columns:minmax(0,1fr)}}
.question-card{{display:flex;flex-direction:column;min-width:0;margin:0}}
.question-card .chart-box{{flex:0 0 auto;display:flex;flex-direction:column;align-items:stretch;height:var(--chart-height,266px);padding:0 0 {CHART_LABEL_GAP_PX}px}}
.question-card .chart-box>svg.line-chart{{flex:1 1 auto;height:100%;min-height:0}}
.question-card .chart-box>.vbars{{flex:1 1 auto;height:100%;min-height:0;margin-bottom:0}}
.question-card .chart-box>.bar-chart{{flex:1 1 auto;height:100%;min-height:0}}
@media(max-width:760px){{header,main section,main>.section-row,footer{{padding-left:22px;padding-right:22px}}.kpis{{grid-template-columns:repeat(2,minmax(0,1fr))}}.kpi{{padding:16px 12px}}.kpi strong{{font-size:22px}}.section-row,.findings,.two-col,.dimensions,.method{{grid-template-columns:minmax(0,1fr)}}.bar-row{{grid-template-columns:minmax(80px,1.1fr) minmax(60px,1fr) 82px;gap:7px}}h1{{font-size:28px}}}}
@media(max-width:420px){{.kpis{{grid-template-columns:1fr}}.kpi strong{{font-size:22px}}.basis{{gap:10px}}.basis span{{flex:1 1 100%}}}}
.chart-source{{font-size:11px;color:#888;margin:2px 0 10px}}.tab-note{{font-size:11px;color:#666;margin:16px 0 0}}.chart-conclusion{{font-size:12px;color:#444;border-left:3px solid var(--primary);padding:6px 0 6px 10px;margin:12px 0 0}}.chart-title{{margin:0}}.question-narrative{{font-size:14px;color:#333;margin:0 0 14px}}
@media print{{body{{background:white}}.page{{max-width:none}}}}
</style></head><body><div class="page">
<header><div class="eyebrow">{esc(report_subtitle)}</div><h1>{esc(report_title)}</h1><div class="subtitle">分析期间：{esc(start)} 至 {esc(end)} · 面向{esc(audience)}</div>
<div class="basis">{basis_html}</div></header>
<main>{conclusions_html}<section><h2>经营概览</h2><div class="kpis">{kpi_html}</div>{tab_note}</section>
{sections_html or '<section><h2>结论</h2><p class="empty">本次未产出图表</p></section>'}
{prune_note}
<section><h2>附录：明细、方法与追溯</h2>
<div class="table-wrap"><table><thead><tr><th>{esc(primary_entity_label)}</th><th>{esc(sales_label)}</th><th>{esc(profit_label)}</th><th>{esc(margin_label)}</th><th>{esc(order_label)}</th><th>负{esc(profit_label)}行占比</th></tr></thead><tbody>{dept_rows or '<tr><td colspan="6">暂无明细数据</td></tr>'}</tbody></table></div>
<div class="method"><article><h3>异常概览</h3><ul><li>异常清单共 {integer(anomaly_count)} 行，规则来自本次执行。</li><li>负{esc(profit_label)}明细 {integer(summary['negative_pricing_profit_lines'])} 行。</li><li>{esc(sales_label)}为 0 或订单号缺失的记录进入复核范围。</li></ul></article><article><h3>方法说明</h3><ul><li>金额按 SKU 明细汇总，订单数按{esc(order_field)}去重。</li><li>利润率分母为 0 时标记为不可用。</li><li>报告只读取本次执行快照与汇总产物。</li></ul></article></div>
<div class="table-wrap"><table><thead><tr><th>级别</th><th>字段</th><th>问题</th><th>建议</th></tr></thead><tbody>{''.join(quality_rows) or '<tr><td colspan="4">暂无附加数据质量问题</td></tr>'}</tbody></table></div>
<div class="method"><article><h3>已确认口径</h3><ul>{''.join(f'<li>{esc(item)}</li>' for item in task.get('assumptions', []))}</ul></article><article><h3>运行追溯</h3><ul><li>源数据 SHA-256：<code>{esc(snapshot['source_sha256'])}</code></li><li>源数据 {integer(snapshot['source_rows'])} 行，期间内 {integer(snapshot['scope_rows'])} 行。</li><li>报告生成时间：{esc(generated_at)}</li></ul></article></div></section></main>
<footer>本报告由任务配置和本次运行产物自动生成。所有核心数值应以配套 CSV、Parquet 与运行清单为准。</footer></div></body></html>"""
    report_path = output / "management_report.html"
    report_path.write_text(report, encoding="utf-8")
    return {"report": str(report_path), "summary": str(output / "analysis_summary.json"), "anomaly_rows": anomaly_count}


def main() -> int:
    parser = argparse.ArgumentParser(description="从本次执行产物生成自包含 HTML 报告")
    parser.add_argument("--task", type=Path, default=DEFAULT_TASK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    task_path, output = args.task.resolve(), args.output.resolve()
    build_analysis_summary(task_path, output)
    info = render_report(task_path, output)
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
