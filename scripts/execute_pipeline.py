#!/usr/bin/env python3
"""通用数据分析流水线执行器：支持任意任务配置，保持订单案例向后兼容。"""
from __future__ import annotations
import argparse, hashlib, importlib, importlib.util, json, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.metric_engine import MetricAggregator
from runtime.anomaly_engine import evaluate_rules
from runtime.source_reader import read_scoped_file
from runtime.task_runtime import MetricPlan, time_bounds

DEFAULT_TASK = ROOT / "my_case" / "task.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "order_analysis_2026-06_07"
CHUNK_SIZE = 80_000


def _resolve_prepared_prefix(domain: str | None) -> str:
    """根据 task.domain 推断 parquet 文件名前缀；sales 域保持 'prepared_orders_' 兼容。"""
    if domain == "sales":
        return "prepared_orders_"
    return "prepared_data_"


def _resolve_date_logical(task: dict) -> str:
    """从 task 中提取日期逻辑列名；若 data_source.date_field 缺失则向下兼容为 'opration_date'。"""
    ds = task.get("data_source", {})
    date_logical = ds.get("date_field")
    if not date_logical:
        date_logical = "opration_date"
    return date_logical


def _resolve_derived_dimensions(task: dict, mapping: dict) -> list[dict]:
    """提取 derived_dimensions 配置；若缺失但映射中含 group_first/group_second 则自动生成 department concat。"""
    ds = task.get("data_source", {})
    derived = ds.get("derived_dimensions") or []
    if not derived and "group_first" in mapping and "group_second" in mapping:
        derived = [
            {"name": "department", "type": "concat", "sources": ["group_first", "group_second"], "separator": " / "}
        ]
    return derived


def _resolve_primary_dimension(task: dict, dimensions: list[str], derived: list[dict]) -> str | None:
    """按优先级决定 primary_dimension：output.primary_dimension > 首个 concat 输出 > dimensions[0]。"""
    explicit = task.get("output", {}).get("primary_dimension")
    if explicit:
        return explicit
    for spec in derived:
        if spec.get("type") == "concat":
            return spec["name"]
    return dimensions[0] if dimensions else None


def _source_fingerprint(source: Path) -> str:
    """源文件廉价指纹：文件大小 + mtime（秒级），用于判断是否需要重算全量 SHA256。"""
    stat = source.stat()
    return f"{stat.st_size}:{int(stat.st_mtime)}"


def _source_sha256(source: Path, snapshot_path: Path) -> tuple[str, str]:
    """返回 (sha256, fingerprint)。

    若已有执行快照且其 fingerprint 与当前源文件一致，则直接复用快照中的 sha256，
    避免每次运行都对大文件做全文哈希；否则才真正重算。
    """
    fingerprint = _source_fingerprint(source)
    if snapshot_path.is_file():
        try:
            previous = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        if previous.get("source_fingerprint") == fingerprint and previous.get("source_sha256"):
            return str(previous["source_sha256"]), fingerprint

    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest(), fingerprint


def _anomaly_columns(
    chunk: pd.DataFrame,
    order_source: str,
    date_logical: str,
    sales_source: str,
    profit_source: str,
    dimensions: list[str],
) -> list[str]:
    """按存在性组合异常 CSV 列：订单源字段 + 维度 + 指标源字段 + 日期逻辑列。"""
    cols = [order_source]
    for d in dimensions:
        if d in chunk.columns and d not in cols:
            cols.append(d)
    for extra in ["analysis_month", date_logical, sales_source, profit_source, "quantity", "real_refund"]:
        if extra in chunk.columns and extra not in cols:
            cols.append(extra)
    return [c for c in cols if c in chunk.columns]


def _load_hooks(hooks_arg: str | None):
    """从文件路径或模块路径加载本地 Agent 推理钩子；未指定时回退 LocalAgentHooks 基类。

    hooks 入口需提供 build_hooks() 函数，返回 LocalAgentHooks 实例。基类未覆写推理节点
    时，local_insight / charting / synthesis 会抛 AgentReasoningRequired，绝不回退模板。
    """
    if not hooks_arg:
        from runtime.node_hooks import LocalAgentHooks
        return LocalAgentHooks()
    path = Path(hooks_arg)
    if path.is_file():
        spec = importlib.util.spec_from_file_location("_external_agent_hooks", path)
        if spec is None or spec.loader is None:
            raise SystemExit(f"无法加载 hooks 文件：{hooks_arg}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(hooks_arg)
    build = getattr(module, "build_hooks", None)
    if build is None:
        raise SystemExit(f"hooks 入口 {hooks_arg} 未提供 build_hooks() 函数")
    return build()


def run(task_path: Path, output_dir: Path, hooks: object | None = None) -> dict:
    """通用流水线核心入口：加载任务 → 读取并映射数据 → 指标聚合 → 异常落盘 → 报告/运行清单。

    参数 hooks：本地 Agent 推理钩子；默认 None 时使用 LocalAgentHooks 基类，
    未覆写推理节点的实现会在 local_insight / synthesis 处抛 AgentReasoningRequired。
    返回 execution_snapshot 字典。
    """
    task_path = task_path.resolve()
    output = output_dir.resolve()
    task = json.loads(task_path.read_text(encoding="utf-8"))
    if hooks is None:
        from runtime.node_hooks import LocalAgentHooks
        hooks = LocalAgentHooks()

    mapping = task["data_source"].get("field_mapping", {})
    source = (task_path.parent / task["data_source"]["location"]).resolve()
    start, end, period = time_bounds(task["time_range"])
    output.mkdir(parents=True, exist_ok=True)

    plan = MetricPlan.from_task(task)
    metric_defs = plan.definitions
    sales, profit, order_metric = plan.sales, plan.profit, plan.order
    sales_source = metric_defs[sales]["source_field"]
    profit_source = metric_defs[profit]["source_field"]
    order_source = plan.order_source
    anomaly_rules = task.get("anomaly_rules", [])

    date_logical = _resolve_date_logical(task)
    derived = _resolve_derived_dimensions(task, mapping)
    dimensions_raw = list(dict.fromkeys(task.get("dimensions", [])))
    derived_out_names = [spec["name"] for spec in derived]
    dimensions = list(dict.fromkeys([*dimensions_raw, *derived_out_names]))
    primary_dimension = _resolve_primary_dimension(task, dimensions, derived)
    profit_source_field = profit_source

    required = {date_logical, *[spec["source_field"] for spec in plan.base.values()], *dimensions_raw, *[rule["field"] for rule in anomaly_rules]}
    missing = required - set(mapping)
    if missing:
        raise SystemExit(f"任务字段映射缺失：{sorted(missing)}")

    snapshot_path = output / "execution_snapshot.json"
    source_sha256, source_fingerprint = _source_sha256(source, snapshot_path)

    prefix = _resolve_prepared_prefix(task.get("domain"))
    prepared = output / f"{prefix}{period.replace('-', '_')}.parquet"
    anomaly = output / "anomaly_orders.csv"
    for p in {prepared, anomaly}:
        if p.exists():
            p.unlink()

    writer = None
    rows = scope = 0
    anomaly_header = True
    aggregator = MetricAggregator(
        plan, dimensions,
        primary_dimension=primary_dimension,
        profit_source_field=profit_source_field,
    )
    numeric = plan.numeric_sources | ({"quantity", "real_refund"} & set(mapping))

    for raw_count, chunk in read_scoped_file(
        source, task["data_source"], numeric, start, end, CHUNK_SIZE,
        date_logical=date_logical,
        derived_dimensions=derived,
    ):
        rows += raw_count
        if chunk.empty:
            continue
        scope += len(chunk)
        chunk["line_count"] = 1
        if profit_source in chunk.columns:
            chunk["negative_profit_line_count"] = chunk[profit_source].lt(0).astype(int)
            chunk["negative_calc_profit_line_count"] = chunk["negative_profit_line_count"]
        aggregator.consume(chunk)
        bad, anomaly_labels = evaluate_rules(chunk, anomaly_rules)
        if bad.any():
            cols = _anomaly_columns(chunk, order_source, date_logical, sales_source, profit_source, dimensions)
            selected = chunk.loc[bad, cols].copy()
            selected.insert(1, "anomaly_types", anomaly_labels.loc[bad])
            selected.to_csv(
                anomaly, mode="a", index=False, header=anomaly_header,
                encoding="utf-8-sig" if anomaly_header else "utf-8",
            )
            anomaly_header = False
        table = pa.Table.from_pandas(chunk, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(prepared, table.schema, compression="zstd")
        writer.write_table(table)
    if writer:
        writer.close()

    summaries, monthly_primary = aggregator.summaries()
    for dim, result in summaries.items():
        if dim == "analysis_month":
            filename = "monthly_summary.csv"
        elif dim == "department":
            filename = "department_summary.csv"
        else:
            filename = f"dimension_{dim}.csv"
        result.to_csv(output / filename, index=False, encoding="utf-8-sig")
        # 对 primary_dimension 额外生成 dimension_{name}.csv 新名（如 department 已写 department_summary.csv 则补新名）
        if dim == primary_dimension and filename != f"dimension_{dim}.csv":
            result.to_csv(output / f"dimension_{dim}.csv", index=False, encoding="utf-8-sig")

    monthly = output / "monthly_summary.csv"
    dept_csv = output / "department_summary.csv"
    if monthly_primary is not None:
        if primary_dimension == "department":
            monthly_primary.to_csv(output / "monthly_department_summary.csv", index=False, encoding="utf-8-sig")
        else:
            # 通用命名：monthly_{primary_dimension}_summary.csv，同时保留别名
            mp_name = f"monthly_{primary_dimension}_summary.csv"
            monthly_primary.to_csv(output / mp_name, index=False, encoding="utf-8-sig")
            if not (output / "monthly_department_summary.csv").exists():
                monthly_primary.to_csv(output / "monthly_department_summary.csv", index=False, encoding="utf-8-sig")
    elif monthly.exists() and dept_csv.exists():
        pd.read_csv(monthly).to_csv(output / "monthly_department_summary.csv", index=False, encoding="utf-8-sig")

    snapshot = {
        "schema_version": "1.3",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task": str(task_path),
        "source": str(source),
        "source_sha256": source_sha256,
        "source_fingerprint": source_fingerprint,
        "source_rows": rows,
        "scope_rows": scope,
        "prepared_data": prepared.name,
        "analysis_period": {
            "start": str(start.date()),
            "end_exclusive": str(end.date()),
            "date_field": date_logical,
        },
        "basis": {
            "sales": sales,
            "sales_unit": metric_defs[sales].get("unit"),
            "profit": profit,
            "profit_source_field": profit_source_field,
        },
        "primary_dimension": primary_dimension,
        "derived_dimensions": derived,
        "dimensions": dimensions,
        "metrics": list(metric_defs.values()),
    }
    (output / "execution_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    # 分三段执行：先构建口径摘要（供 build_run 读取），再生成 10 节点运行清单（含本地 Agent 洞察），
    # 最后渲染报告——这样报告能纳入 synthesis 的 L1 结论页与建议动作。
    from scripts.generate_html_report import build_analysis_summary, render_report
    from scripts.build_approved_run import build_run
    build_analysis_summary(task_path, output)
    build_run(task_path, output, hooks)
    render_report(task_path, output)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return snapshot


def main() -> int:
    ap = argparse.ArgumentParser(description="通用数据分析流水线（兼容订单案例）")
    ap.add_argument("--task", type=Path, default=DEFAULT_TASK)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--hooks", type=str, default=None, help="本地 Agent 推理钩子的文件或模块路径（需提供 build_hooks()）")
    a = ap.parse_args()
    run(a.task, a.output, hooks=_load_hooks(a.hooks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
