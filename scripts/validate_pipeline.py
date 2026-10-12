#!/usr/bin/env python3
"""检查工作流节点、提示词和输出 Schema 的引用完整性。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


EXTERNAL_TARGETS = {"ask_user", "complete"}


def main() -> int:
    parser = argparse.ArgumentParser(description="校验数据分析工作流配置")
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Skill 根目录",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    pipeline_path = root / "workflow" / "pipeline.yaml"

    try:
        pipeline = yaml.safe_load(pipeline_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(f"无法读取工作流：{exc}", file=sys.stderr)
        return 2

    errors: list[str] = []
    nodes = pipeline.get("nodes", []) if isinstance(pipeline, dict) else []
    ids = [node.get("id") for node in nodes if isinstance(node, dict)]
    id_set = set(ids)

    if len(ids) != len(id_set):
        errors.append("节点 id 存在重复")

    if pipeline.get("entrypoint") not in id_set:
        errors.append(f"entrypoint 指向不存在的节点：{pipeline.get('entrypoint')}")
    if pipeline.get("terminal") not in id_set and pipeline.get("terminal") not in EXTERNAL_TARGETS:
        errors.append(f"terminal 指向不存在的节点或外部终点：{pipeline.get('terminal')}")

    for node in nodes:
        node_id = node.get("id", "<unknown>")
        for field in ("prompt", "output_schema"):
            ref = node.get(field)
            if not ref:
                errors.append(f"节点 {node_id} 缺少 {field}")
                continue
            path = root / ref
            if not path.is_file():
                errors.append(f"节点 {node_id} 的 {field} 不存在：{ref}")
            elif field == "output_schema":
                try:
                    schema = json.loads(path.read_text(encoding="utf-8"))
                    Draft202012Validator.check_schema(schema)
                except Exception as exc:
                    errors.append(f"节点 {node_id} 的 Schema 无效：{exc}")

        for route_type in ("on_success", "on_failure"):
            routes = node.get(route_type)
            if routes is None:
                errors.append(f"节点 {node_id} 缺少 {route_type}")
                continue
            if isinstance(routes, str):
                routes = [routes]
            if not isinstance(routes, list) or not routes:
                errors.append(f"节点 {node_id} 的 {route_type} 必须是非空字符串或数组")
                continue
            for route in routes:
                if route not in id_set and route not in EXTERNAL_TARGETS:
                    errors.append(f"节点 {node_id} 的 {route_type} 无效：{route}")

    if errors:
        print("工作流校验失败：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"工作流校验通过：{len(nodes)} 个节点，所有引用有效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
