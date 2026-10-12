#!/usr/bin/env python3
"""校验完整运行清单、节点产物及其跨节点引用。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from validate_artifact import load_json, semantic_errors


TIME_DIMENSIONS = {"time", "date", "day", "week", "month", "quarter", "year"}
CHART_FILE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


def resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def unique_ids(items: list[object], label: str, errors: list[str]) -> set[str]:
    ids = [item.get("id") for item in items if isinstance(item, dict)]
    if len(ids) != len(set(ids)):
        errors.append(f"{label} 存在重复 id")
    return {value for value in ids if isinstance(value, str)}


def validate_with_schema(
    schema_path: Path,
    artifact_path: Path,
    errors: list[str],
    artifact_base: Path | None = None,
) -> object | None:
    try:
        schema = load_json(schema_path)
        instance = load_json(artifact_path)
    except ValueError as exc:
        errors.append(str(exc))
        return None

    validator = Draft202012Validator(schema)
    for error in validator.iter_errors(instance):
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        errors.append(f"{artifact_path.name}:{location}: {error.message}")
    for message in semantic_errors(schema_path, instance, artifact_base or artifact_path.parent):
        errors.append(f"{artifact_path.name}: {message}")
    return instance


def main() -> int:
    parser = argparse.ArgumentParser(description="校验数据分析端到端运行清单")
    parser.add_argument("manifest", type=Path, help="运行清单 JSON 文件")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Skill 根目录")
    args = parser.parse_args()
    root = args.root.resolve()
    manifest_path = args.manifest.resolve()
    base = manifest_path.parent
    errors: list[str] = []

    manifest = validate_with_schema(root / "schemas/run-manifest.schema.json", manifest_path, errors, base)
    if not isinstance(manifest, dict):
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    pipeline = yaml.safe_load((root / "workflow/pipeline.yaml").read_text(encoding="utf-8"))
    node_map = {node["id"]: node for node in pipeline["nodes"]}
    artifacts: dict[str, object] = {}

    task_path = resolve(base, manifest["task_config"])
    task = validate_with_schema(root / "config/task.schema.json", task_path, errors, base)

    for node_id, value in manifest["artifacts"].items():
        artifact_path = resolve(base, value)
        node = node_map[node_id]
        artifacts[node_id] = validate_with_schema(
            root / node["output_schema"], artifact_path, errors, base
        )

    if not isinstance(task, dict) or any(not isinstance(value, dict) for value in artifacts.values()):
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    intake = artifacts["intake"]
    if intake.get("task_config") != task:
        errors.append("intake.task_config 与清单中的 task_config 不一致")

    expected_success = {
        "intake": ("status", "ready"),
        "planning": ("status", "ready"),
        "preparation": ("status", "ready"),
        "extraction": ("status", "ready"),
        "data_validation": ("passed", True),
        "local_insight": ("status", "ready"),
        "charting": ("status", "ready"),
        "synthesis": ("status", "ready"),
        "reporting": ("status", "ready"),
        "final_review": ("passed", True),
    }
    for node_id, (field, expected) in expected_success.items():
        if artifacts[node_id].get(field) != expected:
            errors.append(
                f"完整交付清单要求节点 {node_id}.{field}={expected!r}；失败运行不得标记为 complete"
            )

    plan = artifacts["planning"]
    planned = plan.get("datasets", [])
    plan_ids = unique_ids(planned, "分析计划数据集", errors)
    execution_order = plan.get("execution_order", [])
    if set(execution_order) != plan_ids or len(execution_order) != len(plan_ids):
        errors.append("planning.execution_order 必须无重复且完整引用计划数据集")

    task_metrics = {metric["name"] for metric in task.get("metrics", [])}
    task_dimensions = set(task.get("dimensions", [])) | TIME_DIMENSIONS
    for dataset in planned:
        extra_metrics = set(dataset.get("metrics", [])) - task_metrics
        extra_dimensions = set(dataset.get("dimensions", [])) - task_dimensions
        if extra_metrics:
            errors.append(f"计划数据集 {dataset.get('id')} 包含未确认指标：{sorted(extra_metrics)}")
        if extra_dimensions:
            errors.append(f"计划数据集 {dataset.get('id')} 包含未确认维度：{sorted(extra_dimensions)}")

    dataset_ids = unique_ids(artifacts["extraction"].get("datasets", []), "指标数据集", errors)
    if dataset_ids != plan_ids:
        errors.append("extraction.datasets 必须完整覆盖且仅覆盖计划数据集")

    validation = artifacts["data_validation"]
    if validation.get("passed") and set(validation.get("validated_dataset_ids", [])) != dataset_ids:
        errors.append("校验通过时 validated_dataset_ids 必须完整覆盖指标数据集")

    insights = artifacts["local_insight"].get("insights", [])
    insight_ids = unique_ids(insights, "局部洞察", errors)
    for insight in insights:
        if insight.get("dataset_id") not in dataset_ids:
            errors.append(f"局部洞察 {insight.get('id')} 引用了不存在的数据集")

    charts = artifacts["charting"].get("charts", [])
    chart_ids = unique_ids(charts, "图表", errors)
    include_charts = task.get("output", {}).get("include_charts", True)
    if include_charts and not charts:
        errors.append("output.include_charts=true 时必须生成至少一个图表")
    if not include_charts and charts:
        errors.append("output.include_charts=false 时不得生成图表")
    for chart in charts:
        if chart.get("dataset_id") not in dataset_ids:
            errors.append(f"图表 {chart.get('id')} 引用了不存在的数据集")
        if chart.get("insight_id") not in insight_ids:
            errors.append(f"图表 {chart.get('id')} 引用了不存在的局部洞察")

    synthesis = artifacts["synthesis"]
    if not include_charts and synthesis.get("selected_chart_ids"):
        errors.append("output.include_charts=false 时 selected_chart_ids 必须为空")
    if not set(synthesis.get("selected_chart_ids", [])).issubset(chart_ids):
        errors.append("selected_chart_ids 包含不存在的图表")
    evidence_pool = dataset_ids | insight_ids | chart_ids
    for insight in synthesis.get("global_insights", []):
        missing = set(insight.get("evidence_refs", [])) - evidence_pool
        if missing:
            errors.append(f"全局洞察 {insight.get('id')} 包含不存在的证据引用：{sorted(missing)}")
    for decision in synthesis.get("design_decisions", []):
        if decision.get("chart_id") not in chart_ids:
            errors.append(f"设计决策引用不存在的图表：{decision.get('chart_id')}")

    report = artifacts["reporting"].get("report", {})
    final = artifacts["final_review"]
    if report.get("format") != task.get("output", {}).get("format"):
        errors.append("报告格式必须与 task_config.output.format 一致")
    if not include_charts and any(
        item.get("type") == "chart"
        for item in final.get("deliverables", [])
        if isinstance(item, dict)
    ):
        errors.append("output.include_charts=false 时不得包含 chart 类型交付物")
    if not include_charts:
        for item in final.get("deliverables", []):
            if not isinstance(item, dict):
                continue
            suffix = Path(item.get("path", "")).suffix.lower()
            description = item.get("description", "").lower()
            if suffix in CHART_FILE_SUFFIXES or "图表" in description or "chart" in description:
                errors.append("output.include_charts=false 时不得用其他类型夹带图表或图像交付物")
    if final.get("passed"):
        report_path = resolve(base, report.get("artifact_ref", ""))
        deliverable_paths = {resolve(base, item["path"]) for item in final.get("deliverables", [])}
        if report_path not in deliverable_paths:
            errors.append("最终交付物必须包含 reporting.report.artifact_ref")

    if errors:
        print("端到端运行校验失败：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("端到端运行校验通过：任务范围、节点契约、引用关系和交付物均有效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
