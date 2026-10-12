# -*- coding: utf-8 -*-
"""把案例源文件（xlsx）无损转换为「数值列统一为 float64」的等值 Parquet 数据源。

为什么需要这一步
================
Skill 的读取层（`runtime/source_reader.py`）对流式分块读取的每个 chunk 独立做
`pd.to_numeric`，因此当某列（如 advertising_fee）在部分月份的取值恰好都是整数时，
该 chunk 会被推断为 int64，而其他 chunk 为 float64；两份 chunk 的 Arrow schema 不一致，
`pyarrow.ParquetWriter` 在写入第二个 schema 时会抛 ArrowInvalid（schema 冲突），
导致全量读取中断。

本脚本不修改任何 Skill 文件，只是在案例目录内生成一个**等值**的数据源：
- 逐块读取源 xlsx（复用与 Skill 相同的 openpyxl 只读流式解析路径）；
- 仅做「数值列 → float64 / 文本列 → string」的类型统一，**不改变任何数值**；
- 用固定 Arrow schema 写出 Parquet，保证每个 chunk 的类型一致。

转换后 `task.json` 的 `data_source.location` 指向该 Parquet。
校验方式：转换结果的 行数 / 销售额 / 计价利润 必须与直接读取源 xlsx 的结果一致
（见脚本末尾打印与 `_verify_against_prepared` 的对照说明）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

CASE_DIR = Path(__file__).resolve().parent
SKILL_ROOT = CASE_DIR.parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime.source_reader import _excel_chunks  # noqa: E402

CHUNK_SIZE = 80_000
TEXT_FIELDS = {"pay_time", "order_number", "group_first", "group_second", "country_code", "order_type"}
# 原始数据源固定指向 Skill 的 temp 目录（与 task.json 的 data_source 解耦，避免自我引用）。
SOURCE_XLSX = SKILL_ROOT / "temp" / "电商订单明细数据.xlsx"


def numeric_logical_fields(task: dict) -> set[str]:
    """需要按数值处理的逻辑列 = 所有 sum 聚合指标的源字段 + quantity / real_refund。"""
    fields = {
        metric["source_field"]
        for metric in task.get("metrics", [])
        if metric.get("source_field") and metric.get("aggregation") == "sum"
    }
    fields |= {"quantity", "real_refund"}
    return fields


def main() -> int:
    task = json.loads((CASE_DIR / "task.json").read_text(encoding="utf-8"))
    source = SOURCE_XLSX
    mapping = task["data_source"]["field_mapping"]
    rename = {physical: logical for logical, physical in mapping.items()}
    numeric = numeric_logical_fields(task) & set(mapping)

    out_dir = CASE_DIR / "source"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "orders_typed.parquet"
    if target.exists():
        target.unlink()

    writer: pq.ParquetWriter | None = None
    schema: pa.Schema | None = None
    rows = 0
    for chunk in _excel_chunks(source, None, CHUNK_SIZE, source.suffix.lower()):
        rows += len(chunk)
        frame = chunk[list(mapping.values())].rename(columns=rename)
        for column in frame.columns:
            if column in numeric:
                frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
            else:
                frame[column] = frame[column].fillna("").astype(str)
        table = pa.Table.from_pandas(frame, preserve_index=False)
        if writer is None:
            schema = table.schema
            writer = pq.ParquetWriter(target, schema, compression="zstd")
        else:
            table = table.cast(schema)
        writer.write_table(table)
        if rows % 400_000 < CHUNK_SIZE:
            print(f"  已转换 {rows:,} 行...", flush=True)
    if writer is not None:
        writer.close()

    print(f"源文件总行数: {rows:,}")
    print(f"输出: {target}")
    check = pd.read_parquet(target)
    print(f"输出行数: {len(check):,}")
    print(f"金额校验 -> amt: {check['amt'].sum():,.2f}; calc_final_profit: {check['calc_final_profit'].sum():,.2f}")
    print("类型校验 ->", {c: str(check[c].dtype) for c in sorted(numeric)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
