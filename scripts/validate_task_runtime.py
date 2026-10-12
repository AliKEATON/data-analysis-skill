#!/usr/bin/env python3
"""执行前校验任务配置与源文件字段，避免无效任务启动全量处理。"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from safe_formula import FormulaError, parse_formula
from runtime.anomaly_engine import AnomalyRuleError, validate_rules

import pandas as pd
import pyarrow.parquet as pq


SUPPORTED_FILE_SUFFIXES = {".csv", ".xlsx", ".xls", ".parquet"}


def read_source_headers(source: Path, sheet: str | None) -> set[str]:
    """从 CSV/XLSX/XLS/Parquet 读取列名集合。"""
    suffix = source.suffix.lower()
    if suffix == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            return set(next(csv.reader(handle)))
    if suffix in {".xlsx", ".xls"}:
        return set(pd.read_excel(source, sheet_name=sheet or 0, nrows=0).columns.astype(str))
    if suffix == ".parquet":
        return set(pq.ParquetFile(source).schema_arrow.names)
    raise ValueError(f"不支持的文件格式 {suffix or '<无扩展名>'}")


def _resolve_required_mapping(task: dict) -> set[str]:
    """基于 role 映射 + date_field 动态生成 REQUIRED_MAPPING，替代原硬编码。"""
    required = set()
    metric_items = task.get("metrics", [])
    metric_by_role = {}
    for metric in metric_items:
        src = metric.get("source_field")
        if src:
            required.add(src)
        role = metric.get("role")
        if role:
            metric_by_role[role] = metric
    role_default_fields = {
        "sales": "amt",
        "profit": "calc_final_profit",
        "order_count": "order_number",
    }
    for role, default_field in role_default_fields.items():
        if role not in metric_by_role:
            required.add(default_field)
        else:
            src = metric_by_role[role].get("source_field")
            if src:
                required.add(src)
    date_field = task.get("data_source", {}).get("date_field") or "opration_date"
    required.add(date_field)
    return required


def main() -> int:
    parser = argparse.ArgumentParser(description="校验任务配置是否可执行")
    parser.add_argument("task", type=Path)
    args = parser.parse_args()
    task_path = args.task.resolve()
    task = json.loads(task_path.read_text(encoding="utf-8"))
    errors: list[str] = []

    metric_items = task.get("metrics", [])
    metrics = {metric.get("name") for metric in metric_items}

    metric_by_name = {metric.get("name"): metric for metric in metric_items}
    required_roles = {"sales", "profit", "order_count", "margin"}
    roles = [metric.get("role") for metric in metric_items if metric.get("role")]
    missing_roles = required_roles - set(roles)
    if missing_roles:
        errors.append(f"缺少指标角色：{sorted(missing_roles)}")
    if "sales" in missing_roles or "profit" in missing_roles:
        errors.append("缺少主指标：必须分别配置 sales 和 profit 指标角色")
    duplicate_roles = sorted({role for role in roles if role != "other" and roles.count(role) > 1})
    if duplicate_roles:
        errors.append(f"指标角色重复：{duplicate_roles}")

    order_metrics = [m for m in metric_items if m.get("role") == "order_count"]
    if order_metrics:
        if not order_metrics[0].get("source_field") or order_metrics[0].get("aggregation") != "count_distinct":
            errors.append(
                f"order_count 角色 {order_metrics[0].get('name')!r} 必须配置 source_field 且 aggregation=count_distinct"
            )

    for metric in metric_items:
        name = metric.get("name", "<未命名>")
        has_source = bool(metric.get("source_field") or metric.get("aggregation"))
        has_formula = bool(metric.get("formula"))
        if not has_source and not has_formula:
            continue
        if has_source and has_formula:
            errors.append(f"指标 {name} 必须且只能配置 source_field+aggregation 或 formula")
            continue
        if has_source and (not metric.get("source_field") or not metric.get("aggregation")):
            errors.append(f"指标 {name} 的 source_field 和 aggregation 必须同时配置")
            continue
        if has_source:
            agg = metric.get("aggregation")
            if agg not in {"sum", "count_distinct", "avg", "max", "min", "count"}:
                errors.append(f"指标 {name} 的聚合方式 {agg!r} 不在允许枚举中")
                continue
            if agg in {"avg", "max", "min", "count"}:
                # 预检仅校验枚举合法；运行时后续扩展；当前仅 sum/count_distinct 真正实现，这里不拦截
                pass
            if agg == "count_distinct":
                src = metric.get("source_field")
                order_src = order_metrics[0].get("source_field") if order_metrics else None
                if order_src and src == order_src:
                    pass  # 合法
                else:
                    # 非订单字段的 count_distinct 允许通用化，不强制拦截
                    pass
        if has_formula:
            if metric.get("zero_denominator") not in {"null", "zero", "error"}:
                errors.append(f"派生指标 {name} 必须配置有效的 zero_denominator")
            try:
                parse_formula(metric["formula"], metrics)
            except FormulaError as exc:
                errors.append(f"派生指标 {name} 无效：{exc}")

    # 仅拦截 source_field 字面值为 final_profit 的历史字段
    if any(metric.get("source_field") == "final_profit" for metric in metric_items):
        errors.append("final_profit 不允许作为任何主指标源字段")
    if "final_profit" in metrics:
        errors.append("final_profit 不允许作为本案例主利润指标名")

    data_source = task.get("data_source", {})
    source_type = data_source.get("type")
    if source_type != "file":
        errors.append(f"当前运行时仅支持 data_source.type=file，不支持 {source_type!r}")
    mapping = data_source.get("field_mapping", {})
    anomaly_rules = task.get("anomaly_rules", [])
    try:
        validate_rules(anomaly_rules, set(mapping))
    except AnomalyRuleError as exc:
        errors.append(str(exc))

    source_fields = {metric.get("source_field") for metric in metric_items if metric.get("source_field")}
    source_fields |= {rule.get("field") for rule in anomaly_rules if rule.get("field")}
    required_mapping = _resolve_required_mapping(task)
    missing_mapping = (required_mapping | source_fields) - set(mapping)
    if missing_mapping:
        errors.append(f"缺少字段映射：{sorted(missing_mapping)}")

    location = data_source.get("location")
    source = (task_path.parent / location).resolve() if location else None
    suffix = source.suffix.lower() if source else ""
    sheet = data_source.get("sheet")
    if suffix not in SUPPORTED_FILE_SUFFIXES:
        errors.append(f"当前运行时仅支持 CSV、XLSX、XLS 和 Parquet 文件，收到：{suffix or '<无扩展名>'}")
    if suffix not in {".xlsx", ".xls"} and sheet is not None:
        errors.append("data_source.sheet 仅适用于 XLSX/XLS 文件；CSV/Parquet 必须省略或设为 null")
    if suffix in {".xlsx", ".xls"} and sheet is not None and (not isinstance(sheet, str) or not sheet.strip()):
        errors.append("Excel 的 data_source.sheet 必须是非空工作表名称或 null（null 表示首个工作表）")
    if source is None or not source.is_file():
        errors.append(f"数据源不存在：{source}")
    elif suffix in SUPPORTED_FILE_SUFFIXES:
        try:
            headers = read_source_headers(source, sheet)
            required_fields = required_mapping | source_fields
            missing_source_fields = {mapping[key] for key in required_fields if key in mapping} - headers
            if missing_source_fields:
                errors.append(f"数据源缺少字段：{sorted(missing_source_fields)}")
        except Exception as exc:
            errors.append(f"无法读取数据源列头（{suffix}）：{exc}")

    output = task.get("output", {})
    if output.get("format") not in {"html", "markdown", "xlsx", "json"}:
        errors.append("当前执行器仅支持 output.format=html/markdown/xlsx/json")
    if output.get("include_charts") is not True:
        errors.append("当前执行器要求 output.include_charts=true")
    time_range = task.get("time_range", {})
    start_value, end_value = time_range.get("start"), time_range.get("end")
    granularity = time_range.get("granularity", "month")
    if not isinstance(start_value, str) or not start_value:
        errors.append("time_range.start 必须是非空字符串")
    if not isinstance(end_value, str) or not end_value:
        errors.append("time_range.end 必须是非空字符串")
    if granularity not in {"month", "day", "year"}:
        errors.append("time_range.granularity 必须为 month、day 或 year")
    if isinstance(start_value, str) and isinstance(end_value, str) and start_value and end_value:
        try:
            if granularity == "month":
                start_dt = datetime.strptime(start_value, "%Y-%m")
                end_dt = datetime.strptime(end_value, "%Y-%m")
            elif granularity == "day":
                start_dt = datetime.strptime(start_value, "%Y-%m-%d")
                end_dt = datetime.strptime(end_value, "%Y-%m-%d")
            else:
                start_dt = datetime.strptime(start_value, "%Y")
                end_dt = datetime.strptime(end_value, "%Y")
            if end_dt < start_dt:
                errors.append("time_range.end 不能早于 start")
        except ValueError:
            errors.append(f"time_range.start/end 不符合 {granularity} 格式")

    if errors:
        print("任务执行预检失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"任务执行预检通过：{task_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
