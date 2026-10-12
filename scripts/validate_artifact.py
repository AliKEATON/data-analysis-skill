#!/usr/bin/env python3
"""根据 JSON Schema 校验节点产物，并检查少量跨字段语义。"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import date
from pathlib import Path

from jsonschema import Draft202012Validator


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 JSON：{path}：{exc}") from exc


def json_path(parts: list[object]) -> str:
    return "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in parts)


def parse_period(value: str, end: bool = False) -> date:
    parts = [int(part) for part in value.split("-")]
    if len(parts) == 1:
        return date(parts[0], 12 if end else 1, 31 if end else 1)
    if len(parts) == 2:
        year, month = parts
        if end:
            if month == 12:
                return date(year, 12, 31)
            return date.fromordinal(date(year, month + 1, 1).toordinal() - 1)
        return date(year, month, 1)
    return date(parts[0], parts[1], parts[2])


def task_config_errors(task: object) -> list[str]:
    if not isinstance(task, dict):
        return []
    errors: list[str] = []
    time_range = task.get("time_range", {})
    try:
        start = parse_period(time_range["start"])
        end = parse_period(time_range["end"], end=True)
        if start > end:
            errors.append("time_range.start 不能晚于 time_range.end")
    except (KeyError, TypeError, ValueError):
        errors.append("time_range.start/end 不是有效日期")

    comparison = task.get("comparison")
    if isinstance(comparison, dict):
        comparison_type = comparison.get("type")
        if comparison_type in {"period_over_period", "year_over_year", "benchmark"} and not comparison.get("baseline"):
            errors.append(f"comparison.type={comparison_type} 时 baseline 必填")
        if comparison_type == "target" and comparison.get("target") is None:
            errors.append("comparison.type=target 时 target 必填")
    return errors


def deliverable_errors(path: Path, output_format: str | None = None) -> list[str]:
    if not path.is_file():
        return [f"产物不存在或不可读：{path}"]
    if path.stat().st_size == 0:
        return [f"产物为空文件：{path}"]

    errors: list[str] = []
    suffix = path.suffix.lower()
    expected = {
        "html": {".html", ".htm"},
        "json": {".json"},
        "markdown": {".md", ".markdown"},
        "xlsx": {".xlsx"},
    }
    if output_format in expected and suffix not in expected[output_format]:
        errors.append(f"产物扩展名与格式 {output_format} 不匹配：{path}")
        return errors

    try:
        if output_format == "html":
            text = path.read_text(encoding="utf-8").lower()
            if "<html" not in text or "<body" not in text:
                errors.append(f"HTML 产物缺少 html/body 结构：{path}")
        elif output_format == "json":
            json.loads(path.read_text(encoding="utf-8"))
        elif output_format == "xlsx":
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                    errors.append(f"XLSX 产物缺少工作簿结构：{path}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        errors.append(f"产物格式无法解析：{path}：{exc}")
    return errors


def semantic_errors(
    schema_path: Path, instance: object, artifact_base: Path | None = None
) -> list[str]:
    if not isinstance(instance, dict):
        return []

    errors: list[str] = []
    artifact_base = artifact_base or Path.cwd()
    name = schema_path.name

    if name == "intake-result.schema.json":
        status = instance.get("status")
        if status == "ready" and not isinstance(instance.get("task_config"), dict):
            errors.append("status=ready 时 task_config 必须是对象")
        elif status == "ready":
            task_schema_path = schema_path.resolve().parents[1] / "config" / "task.schema.json"
            try:
                task_schema = load_json(task_schema_path)
                validator = Draft202012Validator(task_schema)
                for error in sorted(
                    validator.iter_errors(instance["task_config"]),
                    key=lambda item: list(item.absolute_path),
                ):
                    errors.append(
                        f"task_config{json_path(list(error.absolute_path))[1:]}: {error.message}"
                    )
                errors.extend(f"task_config: {message}" for message in task_config_errors(instance["task_config"]))
            except Exception as exc:
                errors.append(f"无法校验 task_config：{exc}")
        if status == "need_clarification" and not instance.get("questions"):
            errors.append("status=need_clarification 时 questions 不能为空")

    if "passed" in instance and isinstance(instance.get("issues"), list):
        has_blocking = any(
            isinstance(issue, dict) and issue.get("severity") == "blocking"
            for issue in instance["issues"]
        )
        if has_blocking and instance.get("passed") is True:
            errors.append("存在 blocking 问题时 passed 不能为 true")
        if instance.get("passed") is False and not has_blocking:
            errors.append("passed=false 时必须至少有一个 blocking 问题")
        if instance.get("passed") is False:
            recovery_nodes = {
                issue.get("recovery_node")
                for issue in instance["issues"]
                if isinstance(issue, dict) and issue.get("severity") == "blocking"
            }
            if recovery_nodes != {instance.get("next_node")}:
                errors.append("所有 blocking issue.recovery_node 必须与顶层 next_node 一致")

    if instance.get("status") == "ready" and instance.get("blocking_issues"):
        errors.append("status=ready 时 blocking_issues 必须为空")

    if instance.get("status") == "blocked":
        issue_fields = ["blocking_issues", "issues"]
        if not any(instance.get(field) for field in issue_fields):
            errors.append("status=blocked 时必须提供阻断问题或 issues")

    if name == "final-package.schema.json" and instance.get("passed") is True:
        for deliverable in instance.get("deliverables", []):
            path_value = deliverable.get("path") if isinstance(deliverable, dict) else None
            if not path_value:
                continue
            path = Path(path_value)
            if not path.is_absolute():
                path = artifact_base / path
            errors.extend(deliverable_errors(path))

    if name == "report.schema.json" and instance.get("status") == "ready":
        artifact_ref = instance.get("report", {}).get("artifact_ref")
        if artifact_ref:
            path = Path(artifact_ref)
            if not path.is_absolute():
                path = artifact_base / path
            errors.extend(deliverable_errors(path, instance.get("report", {}).get("format")))

    if name == "task.schema.json":
        errors.extend(task_config_errors(instance))

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验数据分析节点的 JSON 产物")
    parser.add_argument("schema", type=Path, help="JSON Schema 文件路径")
    parser.add_argument("instance", type=Path, help="待校验 JSON 文件路径")
    args = parser.parse_args()

    try:
        schema = load_json(args.schema)
        instance = load_json(args.instance)
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        print(f"校验器初始化失败：{exc}", file=sys.stderr)
        return 2

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path))
    messages = [f"{json_path(list(error.absolute_path))}: {error.message}" for error in errors]
    messages.extend(semantic_errors(args.schema, instance, args.instance.resolve().parent))

    if messages:
        print("校验失败：", file=sys.stderr)
        for message in messages:
            print(f"- {message}", file=sys.stderr)
        return 1

    print(f"校验通过：{args.instance}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
