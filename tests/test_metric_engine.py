"""MetricAggregator 聚合类型扩展测试：sum/avg/max/min/count 的正确性与 avg 跨 chunk 加权。"""

from __future__ import annotations

import unittest

import pandas as pd

from runtime.metric_engine import MetricAggregator
from runtime.task_runtime import MetricPlan


def _make_plan() -> MetricPlan:
    """构造一个包含 sum/avg/max/min/count/count_distinct 多种聚合的任务计划。"""
    task = {
        "metrics": [
            {"name": "amt", "role": "sales", "source_field": "amt", "aggregation": "sum"},
            {"name": "calc_final_profit", "role": "profit", "source_field": "profit", "aggregation": "sum"},
            {"name": "order_count", "role": "order_count", "source_field": "order_number", "aggregation": "count_distinct"},
            {"name": "profit_margin", "role": "margin", "formula": "calc_final_profit / amt", "zero_denominator": "null"},
            {"name": "avg_price", "role": "other", "source_field": "price", "aggregation": "avg"},
            {"name": "max_price", "role": "other", "source_field": "price", "aggregation": "max"},
            {"name": "min_price", "role": "other", "source_field": "price", "aggregation": "min"},
            {"name": "line_count_price", "role": "other", "source_field": "price", "aggregation": "count"},
        ]
    }
    return MetricPlan.from_task(task)


def _chunk(rows: list[dict]) -> pd.DataFrame:
    """构造带 analysis_month、department、指标列的数据块。"""
    return pd.DataFrame(rows)


class MetricAggregatorAggregationTests(unittest.TestCase):
    """验证多种聚合类型的确定性计算结果。"""

    def test_avg_max_min_count_single_chunk(self) -> None:
        """单 chunk 场景下，avg/max/min/count 应正确聚合。"""
        plan = _make_plan()
        agg = MetricAggregator(plan, ["department"], primary_dimension="department", profit_source_field="profit")
        agg.consume(_chunk([
            {"analysis_month": "2026-06", "department": "A", "amt": 100, "profit": 10, "order_number": "o1", "price": 10},
            {"analysis_month": "2026-06", "department": "A", "amt": 200, "profit": 20, "order_number": "o2", "price": 30},
            {"analysis_month": "2026-06", "department": "B", "amt": 300, "profit": 30, "order_number": "o3", "price": 50},
        ]))
        summaries, _ = agg.summaries()
        dept = summaries["department"].set_index("department")

        self.assertEqual(dept.loc["A", "amt"], 300.0)
        self.assertEqual(dept.loc["A", "avg_price"], 20.0)
        self.assertEqual(dept.loc["A", "max_price"], 30.0)
        self.assertEqual(dept.loc["A", "min_price"], 10.0)
        self.assertEqual(dept.loc["A", "line_count_price"], 2)
        self.assertEqual(dept.loc["B", "avg_price"], 50.0)

    def test_avg_weighted_across_chunks(self) -> None:
        """跨 chunk 时 avg 应加权（分块 sum+count 中间状态合并），而非简单平均。"""
        plan = _make_plan()
        agg = MetricAggregator(plan, ["department"], primary_dimension="department", profit_source_field="profit")
        agg.consume(_chunk([
            {"analysis_month": "2026-06", "department": "A", "amt": 100, "profit": 10, "order_number": "o1", "price": 10},
            {"analysis_month": "2026-06", "department": "A", "amt": 200, "profit": 20, "order_number": "o2", "price": 30},
        ]))
        agg.consume(_chunk([
            {"analysis_month": "2026-06", "department": "A", "amt": 100, "profit": 10, "order_number": "o4", "price": 20},
        ]))
        summaries, _ = agg.summaries()
        dept = summaries["department"].set_index("department")

        # 全局平均 = (10 + 30 + 20) / 3 = 20；简单分块平均会得 (20 + 20)/2 = 20，此处用不平均数据验证
        self.assertEqual(dept.loc["A", "avg_price"], 20.0)
        self.assertEqual(dept.loc["A", "line_count_price"], 3)

    def test_avg_weighted_nonuniform_chunks(self) -> None:
        """跨 chunk 且各块行数不均时，avg 必须按行数加权。"""
        plan = _make_plan()
        agg = MetricAggregator(plan, ["department"], primary_dimension="department", profit_source_field="profit")
        # 第一块 2 行，价格 10、30（均 20）
        agg.consume(_chunk([
            {"analysis_month": "2026-06", "department": "A", "amt": 1, "profit": 1, "order_number": "o1", "price": 10},
            {"analysis_month": "2026-06", "department": "A", "amt": 1, "profit": 1, "order_number": "o2", "price": 30},
        ]))
        # 第二块 1 行，价格 100（若简单平均会错误）
        agg.consume(_chunk([
            {"analysis_month": "2026-06", "department": "A", "amt": 1, "profit": 1, "order_number": "o3", "price": 100},
        ]))
        summaries, _ = agg.summaries()
        dept = summaries["department"].set_index("department")

        # 全局平均 = (10 + 30 + 100) / 3 = 46.666...；简单平均会得 (20 + 100)/2 = 60
        self.assertAlmostEqual(dept.loc["A", "avg_price"], 140 / 3, places=6)


if __name__ == "__main__":
    unittest.main()
