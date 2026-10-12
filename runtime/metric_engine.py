from __future__ import annotations
import numpy as np
import pandas as pd
from .safe_formula import evaluate_formula
from .task_runtime import MetricPlan


class MetricAggregator:
    """通用指标聚合器：按配置的维度集合汇总基础指标并计算派生指标。

    - dimensions：调用方传入的完整维度列表（含逻辑派生维）。
    - primary_dimension：可选的主逻辑维名；若提供则额外产出 "analysis_month × 主逻辑维" 交叉汇总。
    - profit_source_field：role=profit 指标的 source_field；用于动态计算负利润行计数/占比。

    支持聚合类型：sum / count / max / min / avg / count_distinct。
    聚合结果以「指标名」作为输出列名，避免多个指标共享同一 source_field 时的列名冲突；
    avg 采用 "分块 sum + count 中间状态，最终加权" 的方式保证流式处理正确性。
    """

    def __init__(self, plan: MetricPlan, dimensions: list[str], primary_dimension: str | None = None, profit_source_field: str | None = None):
        self.plan = plan
        self.dimensions = list(dict.fromkeys(dimensions))
        self.primary_dimension = primary_dimension
        self.profit_source_field = profit_source_field or (
            plan.definitions[plan.profit]["source_field"] if plan.profit in plan.definitions else None
        )
        self.summary_dimensions = list(dict.fromkeys(["analysis_month", *self.dimensions]))
        self.parts = {d: [] for d in self.summary_dimensions}
        # 订单去重：按维度切片累积 (维度值, 订单号) 片段，而非逐行 Python set；
        # 单维度与「analysis_month × 主维度」各维护一组片段，最终 groupby(...).nunique() 全局去重计数。
        self.order_pairs = {d: [] for d in self.summary_dimensions}
        self.month_primary_parts = []
        self.month_primary_order_pairs: list[pd.DataFrame] = []
        # 按聚合类型收集的指标映射：{指标名: source_field}，在 consume 阶段确定。
        self.sum_metrics: dict[str, str] = {}
        self.count_metrics: dict[str, str] = {}
        self.max_metrics: dict[str, str] = {}
        self.min_metrics: dict[str, str] = {}
        self.avg_metrics: dict[str, str] = {}
        self.extra_sum_fields: list[str] = []

    def _collect_metrics(self, chunk: pd.DataFrame) -> None:
        """按聚合类型收集基础指标（排除 count_distinct），并记录内部固定 sum 字段。"""
        self.sum_metrics = {}
        self.count_metrics = {}
        self.max_metrics = {}
        self.min_metrics = {}
        self.avg_metrics = {}
        for name, spec in self.plan.base.items():
            agg = spec.get("aggregation")
            src = spec["source_field"]
            if agg == "sum":
                self.sum_metrics[name] = src
            elif agg == "count":
                self.count_metrics[name] = src
            elif agg == "max":
                self.max_metrics[name] = src
            elif agg == "min":
                self.min_metrics[name] = src
            elif agg == "avg":
                self.avg_metrics[name] = src
        # 内部固定 sum 字段（行数、负利润计数等），保留字段名作为输出列名。
        self.extra_sum_fields = [c for c in ["quantity", "real_refund", "line_count", "negative_profit_line_count",
                                             "negative_calc_profit_line_count"] if c in chunk.columns]

    def _group_aggregate(self, chunk: pd.DataFrame, group_keys: list[str]) -> pd.DataFrame:
        """按聚合类型对 chunk 分组聚合，输出列名为指标名；avg 生成 sum/count 中间列。"""
        frames: list[pd.DataFrame] = []
        for name, src in self.sum_metrics.items():
            frames.append(chunk.groupby(group_keys, dropna=False)[src].sum().rename(name).to_frame())
        if self.extra_sum_fields:
            frames.append(chunk.groupby(group_keys, dropna=False)[self.extra_sum_fields].sum())
        for name, src in self.count_metrics.items():
            frames.append(chunk.groupby(group_keys, dropna=False)[src].count().rename(name).to_frame())
        for name, src in self.max_metrics.items():
            frames.append(chunk.groupby(group_keys, dropna=False)[src].max().rename(name).to_frame())
        for name, src in self.min_metrics.items():
            frames.append(chunk.groupby(group_keys, dropna=False)[src].min().rename(name).to_frame())
        for name, src in self.avg_metrics.items():
            grouped = chunk.groupby(group_keys, dropna=False)[src]
            frames.append(grouped.sum().rename(f"_avg_sum_{name}").to_frame())
            frames.append(grouped.count().rename(f"_avg_cnt_{name}").to_frame())
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, axis=1).reset_index()

    def _reaggregate(self, combined: pd.DataFrame, group_keys: list[str]) -> pd.DataFrame:
        """合并多个分片后重新聚合；avg 用 sum/count 中间状态加权，其余类型直接合并。"""
        frames: list[pd.DataFrame] = []
        for name, _ in self.sum_metrics.items():
            frames.append(combined.groupby(group_keys, dropna=False)[name].sum().to_frame())
        if self.extra_sum_fields:
            frames.append(combined.groupby(group_keys, dropna=False)[self.extra_sum_fields].sum())
        for name, _ in self.count_metrics.items():
            frames.append(combined.groupby(group_keys, dropna=False)[name].sum().to_frame())
        for name, _ in self.max_metrics.items():
            frames.append(combined.groupby(group_keys, dropna=False)[name].max().to_frame())
        for name, _ in self.min_metrics.items():
            frames.append(combined.groupby(group_keys, dropna=False)[name].min().to_frame())
        for name, _ in self.avg_metrics.items():
            grouped = combined.groupby(group_keys, dropna=False)
            total = grouped[f"_avg_sum_{name}"].sum()
            cnt = grouped[f"_avg_cnt_{name}"].sum()
            frames.append((total / cnt.replace(0, np.nan)).rename(name).to_frame())
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, axis=1).reset_index()

    def _order_columns(self, chunk: pd.DataFrame) -> pd.DataFrame | None:
        """向量化抽出「订单号 + 各汇总维度列」的合法行，供各维度切片累积。

        - 剔除订单号为空/缺失的行，与大 set 版本口径一致；
        - 上游 read_scoped_file 已把映射列统一为 str，故仅对非 object 列做一次 astype，
          避免逐维度重复转换；
        - 不做块内 drop_duplicates：本类数据「行数/去重订单数」≈1.03，块内去重收益可忽略，
          而开销随数据量线性增长；全局去重统一放到 summaries() 用 groupby(...).nunique() 完成，
          既保证跨 chunk 全局去重，又不重复哈希。
        """
        order_field = self.plan.order_source
        if order_field not in chunk.columns:
            return None
        columns = [c for c in dict.fromkeys([order_field, *self.summary_dimensions]) if c in chunk.columns]
        orders = chunk[order_field]
        valid = orders.notna() & orders.astype(str).str.strip().ne("")
        frame = chunk.loc[valid, columns]
        if frame.empty:
            return None
        for column in columns:
            if frame[column].dtype != object:
                frame[column] = frame[column].astype(str)
        return frame

    def _order_slice(self, orders_frame: pd.DataFrame, keys: list[str]) -> pd.DataFrame | None:
        """从已就绪的订单帧中切出 (键..., 订单号) 列，作为全局去重计数的输入片段。"""
        order_field = self.plan.order_source
        columns = [*keys, order_field]
        if any(column not in orders_frame.columns for column in columns):
            return None
        return orders_frame[columns]

    def _order_count_map(self, parts: list[pd.DataFrame], keys: list[str]) -> dict[tuple, int]:
        """全局去重计数：concat 各片段后按 keys 对订单号 nunique，返回 {键元组: 去重订单数}。

        关键：跨 chunk 的同一订单必须在同一次 nunique 中统计，保证全局去重而非逐块相加。
        """
        if not parts:
            return {}
        combined = pd.concat(parts, ignore_index=True)
        grouped = combined.groupby(keys, dropna=False, sort=False)[self.plan.order_source].nunique()
        counts: dict[tuple, int] = {}
        for key, value in grouped.items():
            key_tuple = key if isinstance(key, tuple) else (key,)
            counts[tuple(str(part) for part in key_tuple)] = int(value)
        return counts

    def consume(self, chunk: pd.DataFrame) -> None:
        """消费一个数据 chunk，累加各维度和（analysis_month, primary_dimension）交叉汇总的分片。"""
        # 负利润行计数：基于 profit_source_field < 0 动态生成列名兼容旧值
        if self.profit_source_field and self.profit_source_field in chunk.columns and "negative_profit_line_count" not in chunk.columns:
            chunk["negative_profit_line_count"] = chunk[self.profit_source_field].lt(0).astype(int)
            if "negative_calc_profit_line_count" not in chunk.columns:
                chunk["negative_calc_profit_line_count"] = chunk["negative_profit_line_count"]
        self._collect_metrics(chunk)
        orders_frame = self._order_columns(chunk)

        primary = self.primary_dimension
        if primary and primary in chunk.columns:
            self.month_primary_parts.append(self._group_aggregate(chunk, ["analysis_month", primary]))
            if orders_frame is not None:
                pairs = self._order_slice(orders_frame, ["analysis_month", primary])
                if pairs is not None:
                    self.month_primary_order_pairs.append(pairs)

        for dimension in self.summary_dimensions:
            if dimension not in chunk.columns:
                continue
            self.parts.setdefault(dimension, []).append(self._group_aggregate(chunk, [dimension]))
            if orders_frame is not None:
                pairs = self._order_slice(orders_frame, [dimension])
                if pairs is not None:
                    self.order_pairs.setdefault(dimension, []).append(pairs)

    def _calculated(self, result: pd.DataFrame) -> pd.DataFrame:
        """对汇总结果追加：订单计数、负利润行占比、派生指标、利润率兼容别名。"""
        result[self.plan.order] = result.pop("__order_count")

        # 负利润行占比：优先使用 generic 列名，同时保留历史列名兼容
        neg_col = "negative_profit_line_count" if "negative_profit_line_count" in result.columns else (
            "negative_calc_profit_line_count" if "negative_calc_profit_line_count" in result.columns else None
        )
        if neg_col and "line_count" in result.columns:
            rate_col = "negative_profit_line_rate" if neg_col == "negative_profit_line_count" else "negative_calc_profit_line_rate"
            result[rate_col] = np.where(
                result["line_count"].astype(float).eq(0),
                np.nan,
                result[neg_col] / result["line_count"],
            )
            if neg_col == "negative_profit_line_count" and "negative_calc_profit_line_rate" not in result.columns:
                result["negative_calc_profit_line_rate"] = result[rate_col]
                result["negative_calc_profit_line_count"] = result[neg_col]

        for name, spec in self.plan.derived.items():
            result[name] = evaluate_formula(
                spec["formula"],
                {m: result[m] for m in self.plan.definitions if m in result.columns},
                spec["zero_denominator"],
            )
        margin = next((name for name, spec in self.plan.definitions.items() if spec.get("role") == "margin"), None)
        if margin and margin in result.columns:
            result["calc_profit_margin"] = result[margin]
        return result

    def summaries(self) -> tuple[dict[str, pd.DataFrame], pd.DataFrame | None]:
        """返回各单维度汇总 dict，以及（analysis_month × 主逻辑维）交叉汇总 DataFrame（可能为 None）。"""
        summaries = {}
        for dimension, items in self.parts.items():
            if not items:
                continue
            combined = pd.concat(items, ignore_index=True)
            result = self._reaggregate(combined, [dimension])
            counts = self._order_count_map(self.order_pairs.get(dimension, []), [dimension])
            result["__order_count"] = [counts.get((str(v),), 0) for v in result[dimension]]
            summaries[dimension] = self._calculated(result)

        monthly_primary = None
        if self.month_primary_parts and self.primary_dimension:
            primary = self.primary_dimension
            combined = pd.concat(self.month_primary_parts, ignore_index=True)
            monthly_primary = self._reaggregate(combined, ["analysis_month", primary])
            counts = self._order_count_map(self.month_primary_order_pairs, ["analysis_month", primary])
            monthly_primary["__order_count"] = [
                counts.get((str(m), str(d)), 0)
                for m, d in zip(monthly_primary["analysis_month"], monthly_primary[primary])
            ]
            monthly_primary = self._calculated(monthly_primary)
        return summaries, monthly_primary
