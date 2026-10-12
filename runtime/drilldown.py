"""明细下钻通道：让推理节点（本地 Agent）能按任意维度/指标下钻聚合。

动机：extraction 交给推理节点的是被压扁的月度/部门汇总，洞察只能"复述"。
本模块提供对标准化明细（prepared parquet）的按需下钻能力，Agent 可在
local_insight 中调用 `drill.top_by("sku", "profit_amount", 10)` 主动发现
「哪个 SKU / 国家 / 订单类型在亏」，而不是只能做环比对比。

实现约定：
- 只读取标准化明细，不重新加载原始文件；
- 按需惰性加载，构造时不触碰磁盘（即使文件缺失也不报错，仅在使用时报错）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq


class DetailDrilldown:
    """明细下钻助手：在标准化明细上按维度聚合指定指标。

    - dimensions：可用于分组的维度列（如 department / sku / country_code）。
    - metric_fields：{指标名: 明细中的数值列}，用于求和聚合（如 profit_amount -> profit_amt）。
    """

    def __init__(self, prepared_path: Path, dimensions: list[str], metric_fields: dict[str, str]):
        self.prepared_path = Path(prepared_path)
        self.dimensions = list(dict.fromkeys(dimensions))
        self.metric_fields = dict(metric_fields)
        self._frame: pd.DataFrame | None = None

    def _load(self) -> pd.DataFrame:
        """惰性加载标准化明细，仅取需要的列。"""
        if self._frame is None:
            if not self.prepared_path.is_file():
                raise FileNotFoundError(f"下钻数据不存在：{self.prepared_path}")
            schema_columns = pq.ParquetFile(self.prepared_path).schema_arrow.names
            wanted = [c for c in [*self.dimensions, *self.metric_fields.values()] if c in schema_columns]
            self._frame = pq.read_table(self.prepared_path, columns=wanted).to_pandas()
        return self._frame

    def available_dimensions(self) -> list[str]:
        """返回实际可用的下钻维度。"""
        return [d for d in self.dimensions if d in self._load().columns]

    def available_metrics(self) -> list[str]:
        """返回实际可用的下钻指标名。"""
        columns = self._load().columns
        return [m for m, field in self.metric_fields.items() if field in columns]

    def group(self, by: str, metrics: list[str] | None = None) -> list[dict]:
        """按 by 维度对 metrics 求和，返回按第一个指标降序的分组明细。"""
        frame = self._load()
        if by not in frame.columns:
            raise KeyError(f"下钻维度不存在：{by}；可用维度 {self.available_dimensions()}")
        selected = [m for m in (metrics or self.available_metrics()) if m in self.metric_fields]
        if not selected:
            return []
        agg = {m: (self.metric_fields[m], "sum") for m in selected}
        grouped = frame.groupby(by, dropna=False).agg(**agg).reset_index()
        grouped = grouped.sort_values(selected[0], ascending=False)
        return grouped.to_dict(orient="records")

    def top_by(self, dimension: str, metric: str, n: int = 10, ascending: bool = False) -> list[dict]:
        """按 dimension 分组聚合 metric，返回前 n 条（默认取最大的 n 条）。"""
        frame = self._load()
        if dimension not in frame.columns:
            raise KeyError(f"下钻维度不存在：{dimension}；可用维度 {self.available_dimensions()}")
        if metric not in self.metric_fields:
            raise KeyError(f"下钻指标不存在：{metric}；可用指标 {self.available_metrics()}")
        field = self.metric_fields[metric]
        if field not in frame.columns:
            raise KeyError(f"下钻指标字段不存在：{field}")
        grouped = (
            frame.groupby(dimension, dropna=False)[field]
            .sum()
            .rename(metric)
            .reset_index()
            .sort_values(metric, ascending=ascending)
            .head(n)
        )
        return grouped.to_dict(orient="records")
