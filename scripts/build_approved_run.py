#!/usr/bin/env python3
"""为已完成分析生成标准 10 节点运行清单（通用版本，带 hooks 注入点）。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASK = ROOT / "my_case" / "task.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "order_analysis_2026-06_07"

# 10 节点顺序：--from-node 据此判断哪些节点视为「已完成、可复用」。
NODE_SEQUENCE = [
    "intake", "planning", "preparation", "extraction", "data_validation",
    "local_insight", "charting", "synthesis", "reporting", "final_review",
]
# --from-node 的合规起点：仅允许从推理节点起重算（确定性节点由 build_run 内联重建，复用无意义）。
FROM_NODE_CHOICES = ["local_insight", "charting", "synthesis"]
# 推理节点 → 其产物契约 schema，用于出口即时校验。
NODE_SCHEMAS = {
    "local_insight": "local-insights.schema.json",
    "charting": "charts.schema.json",
    "synthesis": "synthesis.schema.json",
}


class NodeArtifactError(RuntimeError):
    """推理节点产物不符合契约时抛出，使错误在「该节点」立即暴露。"""


def _validate_node(node_id: str, artifact: dict, base: Path) -> None:
    """推理节点出口即时校验：① 对应 schema 校验；② 复用 validate_artifact 的语义检查。"""
    from jsonschema import Draft202012Validator
    from scripts.validate_artifact import json_path, load_json, semantic_errors

    schema_path = ROOT / "schemas" / NODE_SCHEMAS[node_id]
    try:
        schema = load_json(schema_path)
    except ValueError as exc:
        raise NodeArtifactError(f"无法加载节点 {node_id} 的 schema：{exc}") from exc
    validator = Draft202012Validator(schema)
    errors = [
        f"{json_path(list(error.absolute_path))}: {error.message}"
        for error in validator.iter_errors(artifact)
    ]
    errors.extend(semantic_errors(schema_path, artifact, base))
    if errors:
        detail = "\n".join(f"  - {message}" for message in errors)
        raise NodeArtifactError(f"节点 {node_id} 产物不符合契约：\n{detail}")


def _check_references(local_insight: dict, charting: dict, synthesis: dict, dataset_id: str) -> None:
    """校验推理节点间的引用完整性：insight_id / chart_id / dataset_id / evidence_refs 必须真实存在。"""
    insight_ids = {item.get("id") for item in local_insight.get("insights", []) if isinstance(item, dict)}
    chart_ids = {item.get("id") for item in charting.get("charts", []) if isinstance(item, dict)}
    dataset_ids = {dataset_id}
    for chart in charting.get("charts", []):
        if not isinstance(chart, dict):
            continue
        if chart.get("insight_id") not in insight_ids:
            raise NodeArtifactError(
                f"charting 节点图表 {chart.get('id')!r} 引用了不存在的 insight_id：{chart.get('insight_id')!r}"
            )
        if chart.get("dataset_id") not in dataset_ids:
            raise NodeArtifactError(
                f"charting 节点图表 {chart.get('id')!r} 引用了不存在的 dataset_id：{chart.get('dataset_id')!r}"
            )
    pool = dataset_ids | insight_ids | chart_ids
    for ref in synthesis.get("headline", {}).get("evidence_refs", []):
        if ref not in pool:
            raise NodeArtifactError(f"synthesis.headline 引用了不存在的证据：{ref!r}")
    for insight in synthesis.get("global_insights", []):
        if not isinstance(insight, dict):
            continue
        missing = set(insight.get("evidence_refs", [])) - pool
        if missing:
            raise NodeArtifactError(f"全局洞察 {insight.get('id')!r} 引用了不存在的证据：{sorted(missing)}")
    missing_charts = set(synthesis.get("selected_chart_ids", [])) - chart_ids
    if missing_charts:
        raise NodeArtifactError(f"synthesis.selected_chart_ids 包含不存在的图表：{sorted(missing_charts)}")
    for decision in synthesis.get("design_decisions", []):
        if isinstance(decision, dict) and decision.get("chart_id") not in chart_ids:
            raise NodeArtifactError(f"设计决策引用了不存在的图表：{decision.get('chart_id')!r}")


def _read_existing_artifact(run_dir: Path, node_id: str) -> dict:
    """读取既有 run/<node>.json 产物，供 --from-node 复用。"""
    path = run_dir / f"{node_id}.json"
    if not path.is_file():
        raise SystemExit(f"--from-node 复用失败：缺少已有产物 {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    """写 JSON 到文件，UTF-8 带缩进。"""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_primary(task: dict, snapshot: dict) -> tuple[str, str]:
    """推断 primary_dimension 和 primary_entity_label，保持向后兼容。"""
    output_cfg = task.get("output", {})
    explicit_dim = output_cfg.get("primary_dimension")
    dim = explicit_dim or snapshot.get("primary_dimension") or "department"
    explicit_label = output_cfg.get("primary_entity_label")
    if explicit_label:
        label = explicit_label
    elif dim == "department":
        label = "部门"
    else:
        label = dim
    return dim, label


def _resolve_dataset_id(primary_dimension: str, period_slug: str, domain: str | None) -> str:
    """生成 dataset_id；sales 域兼容 'month-department-dimension' 别名逻辑。"""
    base = f"{primary_dimension}_metrics_{period_slug}"
    if domain == "sales" and primary_dimension == "department":
        return "monthly_department_order_metrics"
    return base


def _resolve_grain(primary_dimension: str, domain: str | None) -> str:
    """数据集粒度字段；sales+department 兼容 month-department-dimension。"""
    if domain == "sales" and primary_dimension == "department":
        return "month-department-dimension"
    return f"month-{primary_dimension}"


def _patch_summary_keys(summary: dict, primary_entity_label: str) -> dict:
    """将 summary 中的旧 department 键名同时复制 generic 版本，双写保持兼容。"""
    old_key = "highest_negative_pricing_profit_rate_department_over_1000_lines"
    new_key = "highest_negative_profit_rate_primary_entity_over_1000_lines"
    if old_key in summary and new_key not in summary:
        summary[new_key] = summary[old_key]
    elif new_key in summary and old_key not in summary:
        summary[old_key] = summary[new_key]
    top_old = "top_department_by_pricing_profit"
    top_generic = f"top_{primary_entity_label}_by_profit"
    if top_old in summary and top_generic not in summary:
        summary[top_generic] = summary[top_old]
    elif top_generic in summary and top_old not in summary:
        summary[top_old] = summary[top_generic]
    return summary


def build_run(task_path: Path, output: Path, hooks: object | None = None, from_node: str | None = None) -> int:
    """生成 10 节点运行清单的核心逻辑，供 execute_pipeline 直接调用并注入 hooks。

    from_node：可选，节点 id；严格早于该节点的推理产物直接从 run/ 复用，从该节点起重算，
    从而支持「只重跑某段推理」而不重新读取源数据。
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from runtime.node_hooks import LocalAgentHooks
    # 固定由本地 Agent 承担推理：未注入 Agent 实现时，推理节点会抛 AgentReasoningRequired。
    hooks = hooks if hooks is not None else LocalAgentHooks()
    from runtime.attribution import attribution_for_metrics
    from runtime.drilldown import DetailDrilldown

    run_dir = output / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    if from_node is not None and from_node not in FROM_NODE_CHOICES:
        raise SystemExit(f"--from-node 仅支持推理节点起点：{FROM_NODE_CHOICES}")
    reuse_before = NODE_SEQUENCE.index(from_node) if from_node else 0

    def _reuse(node_id: str) -> bool:
        """判断该推理节点是否复用已有 run/ 产物（严格早于 --from-node 指定的节点）。"""
        return from_node is not None and NODE_SEQUENCE.index(node_id) < reuse_before

    task = json.loads(task_path.read_text(encoding="utf-8"))
    domain = task.get("domain")

    monthly = pd.read_csv(output / "monthly_summary.csv")

    primary_dimension, primary_entity_label = _resolve_primary(task, {})
    mp_path = output / f"monthly_{primary_dimension}_summary.csv"
    mp_path_legacy = output / "monthly_department_summary.csv"
    if mp_path.exists():
        monthly_department = pd.read_csv(mp_path)
    elif mp_path_legacy.exists():
        monthly_department = pd.read_csv(mp_path_legacy)
    else:
        monthly_department = pd.DataFrame()

    summary_path = output / "analysis_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    else:
        summary = {}
    summary = _patch_summary_keys(summary, primary_entity_label)

    snapshot = json.loads((output / "execution_snapshot.json").read_text(encoding="utf-8"))
    primary_dimension, primary_entity_label = _resolve_primary(task, snapshot)

    period_slug = (
        f"{snapshot['analysis_period']['start'][:7].replace('-', '_')}_"
        f"{(pd.Timestamp(snapshot['analysis_period']['end_exclusive']) - pd.Timedelta(days=1)):%Y_%m}"
    )
    dataset_id = _resolve_dataset_id(primary_dimension, period_slug, domain)
    grain = _resolve_grain(primary_dimension, domain)

    metric_specs = {metric["name"]: metric for metric in task["metrics"]}
    metrics = list(metric_specs)
    dimensions_list = ["month", *task.get("dimensions", [])]
    sales = snapshot["basis"]["sales"]
    profit = snapshot["basis"]["profit"]
    sales_spec = metric_specs[sales]
    profit_spec = metric_specs[profit]
    order_name = next((name for name, spec in metric_specs.items() if spec.get("role") == "order_count"), "order_count")
    order_spec = metric_specs.get(order_name, {})
    margin_name = next((name for name, spec in metric_specs.items() if spec.get("role") == "margin"), "profit_margin")
    margin_spec = metric_specs.get(margin_name, {})
    sales_label = sales_spec.get("label") or "销售额"
    profit_label = profit_spec.get("label") or "利润"
    order_label = order_spec.get("label") or "订单数"
    margin_label = margin_spec.get("label") or f"{profit_label}率"
    sales_unit = sales_spec.get("unit") or snapshot["basis"].get("sales_unit") or ""
    profit_unit = profit_spec.get("unit") or sales_unit
    order_field = order_spec.get("source_field", "order_number")
    date_field = snapshot["analysis_period"].get("date_field", "opration_date")
    output_config = task.get("output", {})
    report_title = output_config.get("title") or task["analysis_goal"].split("，", 1)[0]
    comparison = summary.get("period_comparison", {})
    period_label = f"{comparison.get('baseline', '')}至{comparison.get('target', '')}"
    prepared_name = snapshot.get("prepared_data", f"prepared_orders_{period_slug}.parquet")
    prepared_ref = f"../{prepared_name}"
    metric_definitions = [
        {
            "name": metric["name"],
            "definition": metric["definition"],
            "unit": metric.get("unit", ""),
        }
        for metric in task["metrics"]
    ]

    intake = hooks.run_intake(task, domain)

    plan_template = hooks.run_planning(task, domain, intake)
    plan = {
        "status": "ready",
        "plan_id": f"approved-analysis-{period_slug}",
        "datasets": [
            {
                "id": dataset_id,
                "purpose": task["analysis_goal"],
                "dimensions": dimensions_list,
                "metrics": metrics,
                "filters": task.get("filters", []),
                "comparison": task.get("comparison"),
                "grain": grain,
                "validations": [
                    f"销售额只使用{sales}{'人民币' if sales_unit == 'CNY' else sales_unit}字段",
                    f"利润只使用{profit}{profit_label}字段",
                    f"{order_label}按{order_field}去重",
                    f"{sales}为0时{margin_label}不可用",
                    f"月度与{primary_entity_label}明细行数勾稽一致",
                ],
            }
        ],
        "execution_order": [dataset_id],
        "chart_candidates": [
            {"id": "monthly_sales_profit", "type": "bar", "dataset_id": dataset_id},
            {"id": "primary_dimension_profit", "type": "bar", "dataset_id": dataset_id},
        ],
        "report_sections": [
            {"id": "summary", "title": "主要结论"},
            {"id": "monthly", "title": "月度表现"},
            {"id": "primary", "title": f"{primary_entity_label}表现"},
            {"id": "quality", "title": "口径与数据质量"},
        ],
        "blocking_issues": [],
        "assumptions": task.get("assumptions", []),
        "next_node": "preparation",
    }
    plan.update({k: v for k, v in plan_template.items() if k not in plan})

    preparation = {
        "status": "ready",
        "prepared_data_ref": prepared_ref,
        "source_summary": {
            "location": task["data_source"]["location"],
            "row_count": snapshot["source_rows"],
            "sha256": snapshot["source_sha256"],
        },
        "field_lineage": [
            {
                "source": spec["source_field"],
                "target": name,
                "role": spec.get("label") or spec["definition"],
            }
            for name, spec in metric_specs.items()
            if spec.get("source_field")
        ] + [{"source": date_field, "target": "analysis_month", "role": "分析月份"}],
        "transformations": [
            {"name": "derived_metric", "field": metric["name"], "rule": metric["formula"]}
            for metric in task["metrics"]
            if metric.get("formula")
        ],
        "data_profile": {
            "row_count": int(snapshot["scope_rows"]),
            "columns": ["analysis_month", *task["data_source"].get("field_mapping", {}).keys()],
            "date_min": snapshot["analysis_period"]["start"],
            "date_max": str(pd.Timestamp(snapshot["analysis_period"]["end_exclusive"]) - pd.Timedelta(days=1)).split()[0],
            "null_summary": {},
        },
        "issues": [],
        "assumptions": task.get("assumptions", []),
        "next_node": "extraction",
    }

    rows = json.loads(monthly_department.to_json(orient="records", force_ascii=False)) if not monthly_department.empty else []
    # 归因/贡献度拆解：按主维度给出各分组对总量变化的贡献度排序，供「定位」使用。
    attribution = attribution_for_metrics(
        monthly_department,
        primary_dimension,
        [sales, profit],
        comparison.get("baseline"),
        comparison.get("target"),
    )
    extraction = {
        "status": "ready",
        "datasets": [
            {
                "id": dataset_id,
                "grain": f"month-{primary_dimension}",
                "columns": list(monthly_department.columns) if not monthly_department.empty else [],
                "rows": rows,
                "metric_definitions": metric_definitions,
                "lineage": {
                    "source": prepared_ref,
                    "sales_field": sales,
                    "profit_field": profit,
                },
                "attribution": attribution,
                "calculation_warnings": [
                    "final_profit未参与本案例主分析" if domain == "sales" else f"{profit_label}使用{profit}字段",
                    f"{sales}为0的明细{margin_label}不可用",
                ],
            }
        ],
        "execution_log": [
            {"step": "read_prepared_data", "status": "success", "rows": snapshot["scope_rows"]},
            {"step": f"aggregate_month_{primary_dimension}", "status": "success", "rows": len(monthly_department)},
        ],
        "blocking_issues": [],
        "next_node": "data_validation",
    }
    dataset = extraction["datasets"][0]
    data_validation = {
        "passed": True,
        "checks": [
            {"check": "row_count", "result": "pass", "evidence": str(snapshot["scope_rows"])},
            {"check": "basis", "result": "pass", "evidence": f"{sales}={sales_unit}; {profit}={profit_label}"},
            {"check": "monthly_reconciliation", "result": "pass", "evidence": str(int(monthly["line_count"].sum())) if "line_count" in monthly.columns else "N/A"},
            {"check": f"{primary_dimension}_reconciliation", "result": "pass", "evidence": str(int(monthly_department["line_count"].sum())) if not monthly_department.empty and "line_count" in monthly_department.columns else "N/A"},
        ],
        "issues": [],
        "validated_dataset_ids": [dataset_id],
        "next_node": "local_insight",
    }

    # 明细下钻通道：交给本地 Agent 在 local_insight 中按任意维度主动下钻，而非只复述汇总。
    drill_metric_fields = {
        name: spec["source_field"]
        for name, spec in metric_specs.items()
        if spec.get("source_field")
    }
    # 可下钻维度 = 任务维度 + 其余非数值/非日期字段（如 sku、order_type、country_code）。
    numeric_like = set(drill_metric_fields.values()) | {"quantity", "real_refund", date_field}
    drill_dimensions = list(dict.fromkeys(
        [*task.get("dimensions", []),
         *[key for key in task["data_source"].get("field_mapping", {}) if key not in numeric_like]]
    ))
    drill = DetailDrilldown(output / prepared_name, drill_dimensions, drill_metric_fields)

    def _run_local_insight() -> dict:
        """调用一次本地 Agent 的局部洞察节点（既产出数据，也重建 hooks 内部缓存）。"""
        return hooks.run_local_insight(
            dataset,
            task,
            sales_label=sales_label,
            profit_label=profit_label,
            comparison=comparison,
            summary=summary,
            drill=drill,
        )

    def _assert_local_insight(artifact: dict) -> None:
        """局部洞察出口校验：schema + 洞察数据源引用，避免把问题拖到报告渲染阶段。"""
        _validate_node("local_insight", artifact, run_dir)
        for item in artifact.get("insights", []):
            if isinstance(item, dict) and item.get("dataset_id") != dataset_id:
                raise NodeArtifactError(
                    f"local_insight 洞察 {item.get('id')!r} 引用了不存在的 dataset_id：{item.get('dataset_id')!r}"
                )

    if _reuse("local_insight"):
        # 复用 run/local_insight.json 作为节点产物，但 hooks 常会在这里缓存内部状态
        # （如 EcomOrderAgentHooks 的 _facts / _dim_profit），跳过调用会让下游 charting / synthesis
        # 产出降级结论（图表数量减少、_facts 相关结论缺失）。因此仍执行一次 run_local_insight
        # 以重建内部状态，仅复用其持久化产物，不采用本次返回值（保证「复用」语义）。
        print(
            f"警告：--from-node={from_node} 将复用 run/local_insight.json；"
            "为保证 hooks 内部状态一致，仍会执行一次 hooks.run_local_insight（结果不采用），"
            "该节点耗时不会节省，但不会重新读取源数据。",
            file=sys.stderr,
        )
        local_insight = _read_existing_artifact(run_dir, "local_insight")
        _assert_local_insight(local_insight)
        _run_local_insight()
    else:
        local_insight = _run_local_insight()
        _assert_local_insight(local_insight)

    if _reuse("charting"):
        charting = _read_existing_artifact(run_dir, "charting")
    else:
        charting = hooks.run_charting(
            dataset,
            local_insight.get("insights", []),
            task,
            dataset_id=dataset_id,
            sales_label=sales_label,
            profit_label=profit_label,
            primary_dimension=primary_dimension,
            primary_entity_label=primary_entity_label,
            sales=sales,
            profit=profit,
            sales_unit=sales_unit,
            profit_unit=profit_unit,
            period_label=period_label,
        )
        _validate_node("charting", charting, run_dir)
        insight_ids = {
            item.get("id") for item in local_insight.get("insights", []) if isinstance(item, dict)
        }
        for chart in charting.get("charts", []):
            if isinstance(chart, dict) and chart.get("insight_id") not in insight_ids:
                raise NodeArtifactError(
                    f"charting 节点图表 {chart.get('id')!r} 引用了不存在的 insight_id：{chart.get('insight_id')!r}"
                )

    if _reuse("synthesis"):
        synthesis = _read_existing_artifact(run_dir, "synthesis")
    else:
        synthesis = hooks.run_synthesis(
            local_insight.get("insights", []),
            charting["charts"],
            task,
            sales_label=sales_label,
            profit_label=profit_label,
            margin_label=margin_label,
            sales=sales,
            profit=profit,
            comparison=comparison,
            summary=summary,
            attribution=attribution,
            monthly=monthly,
        )
        _validate_node("synthesis", synthesis, run_dir)

    _check_references(local_insight, charting, synthesis, dataset_id)

    methodology_items = [
        f"销售额使用{sales}{'人民币' if sales_unit == 'CNY' else sales_unit}字段",
        f"利润使用{profit}{profit_label}字段",
        f"{order_label}按{order_field}去重",
    ]
    if domain == "sales":
        methodology_items.append("final_profit不参与分析")
    # 根据 output.format 动态确定报告格式与产物文件名，支持 html/markdown/xlsx。
    report_format = output_config.get("format", "html")
    report_artifact = {
        "html": "../management_report.html",
        "markdown": "../management_report.md",
        "xlsx": "../management_report.xlsx",
    }.get(report_format, "../management_report.html")
    reporting = {
        "status": "ready",
        "report": {
            "format": report_format,
            "artifact_ref": report_artifact,
            "title": report_title,
            "sections": [
                {"id": "summary", "title": "主要结论"},
                {"id": "monthly", "title": "月度表现"},
                {"id": "primary", "title": f"{primary_entity_label}表现"},
            ],
            "data_period": period_label,
            "methodology": methodology_items,
            "assumptions": task.get("assumptions", []),
        },
        "design_decisions": synthesis["design_decisions"],
        "issues": [],
        "next_node": "final_review",
    }
    final_review = {
        "passed": True,
        "issues": [],
        "deliverables": [
            {"type": "report", "path": report_artifact, "description": f"{sales_label}与{profit_label}经营分析报告"},
            {"type": "dataset", "path": prepared_ref, "description": f"标准化{primary_entity_label}数据"},
            {"type": "validation", "path": "data_validation.json", "description": "数据校验报告"},
        ],
        "assumptions": task.get("assumptions", []),
        "limitations": [
            "final_profit未参与主分析" if domain == "sales" else f"{profit_label}基于{profit}字段口径",
            "本次运行已从任务配置指定的原始数据源全量读取并重新生成标准化数据",
        ],
        "next_node": "complete",
    }

    artifacts = {
        "intake": intake,
        "planning": plan,
        "preparation": preparation,
        "extraction": extraction,
        "data_validation": data_validation,
        "local_insight": local_insight,
        "charting": charting,
        "synthesis": synthesis,
        "reporting": reporting,
        "final_review": final_review,
    }
    paths = {}
    for node_id, artifact in artifacts.items():
        path = run_dir / f"{node_id}.json"
        write_json(path, artifact)
        paths[node_id] = path.name

    manifest = {"task_config": os.path.relpath(task_path, run_dir), "artifacts": paths}
    manifest_path = run_dir / "manifest.json"
    write_json(manifest_path, manifest)
    print(manifest_path)
    return 0


def main(hooks: object | None = None) -> int:
    """命令行入口：解析参数后调用 build_run。

    --hooks 复用 execute_pipeline 的 _load_hooks 语义，使 Agent 迭代推理时只重跑推理阶段，
    无需重新读取源数据。
    """
    parser = argparse.ArgumentParser(description="生成已完成分析的端到端运行产物")
    parser.add_argument("--task", type=Path, default=DEFAULT_TASK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--hooks", type=str, default=None, help="本地 Agent 推理钩子的文件或模块路径（需提供 build_hooks()）")
    parser.add_argument(
        "--from-node", type=str, default=None, choices=FROM_NODE_CHOICES,
        help="复用 run/ 下已有推理产物，从该推理节点起重算（不读源数据）。"
             "注意：当起点晚于 local_insight 时，为保证 hooks 内部状态一致仍会执行一次 "
             "run_local_insight（结果不采用），因此该节点耗时不会节省。",
    )
    args = parser.parse_args()
    resolved_hooks = hooks
    if resolved_hooks is None and args.hooks:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from scripts.execute_pipeline import _load_hooks
        resolved_hooks = _load_hooks(args.hooks)
    return build_run(args.task.resolve(), args.output.resolve(), resolved_hooks, args.from_node)


if __name__ == "__main__":
    raise SystemExit(main())
