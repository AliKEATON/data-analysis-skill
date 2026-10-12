"""归因贡献度拆解与明细下钻通道的单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from runtime.attribution import contribution_breakdown
from runtime.drilldown import DetailDrilldown


class AttributionTests(unittest.TestCase):
    """验证贡献度拆解的方向、排序与边界。"""

    def test_contribution_rate_and_ordering(self) -> None:
        """分组 delta 应正确、按 |delta| 降序排列，贡献度等于 delta / 总量变化。"""
        frame = pd.DataFrame({
            "analysis_month": ["2026-06", "2026-06", "2026-07", "2026-07"],
            "department": ["A", "B", "A", "B"],
            "profit": [100.0, 50.0, 120.0, 20.0],
        })
        result = contribution_breakdown(frame, "department", "profit", "2026-06", "2026-07")
        self.assertEqual(result["total_change"], -10.0)
        # A: +20，B: -30 → 按 |delta| 降序，B 在前
        self.assertEqual(result["items"][0]["group"], "B")
        self.assertEqual(result["items"][0]["delta"], -30.0)
        self.assertAlmostEqual(result["items"][0]["contribution_rate"], 3.0)
        self.assertEqual(result["items"][1]["group"], "A")
        self.assertAlmostEqual(result["items"][1]["contribution_rate"], -2.0)

    def test_zero_total_change_yields_null_rate(self) -> None:
        """总量变化为 0 时，贡献度记为 null，避免除零。"""
        frame = pd.DataFrame({
            "analysis_month": ["2026-06", "2026-06", "2026-07", "2026-07"],
            "department": ["A", "B", "A", "B"],
            "profit": [100.0, 50.0, 60.0, 90.0],
        })
        result = contribution_breakdown(frame, "department", "profit", "2026-06", "2026-07")
        self.assertEqual(result["total_change"], 0.0)
        self.assertIsNone(result["items"][0]["contribution_rate"])

    def test_missing_dimension_returns_empty(self) -> None:
        """维度缺失时返回空 items，不抛异常。"""
        frame = pd.DataFrame({"analysis_month": ["2026-06"], "profit": [1.0]})
        result = contribution_breakdown(frame, "department", "profit", "2026-06", "2026-07")
        self.assertEqual(result["items"], [])


class DrilldownTests(unittest.TestCase):
    """验证明细下钻通道的惰性加载与聚合。"""

    def test_lazy_construction_does_not_touch_disk(self) -> None:
        """构造时不读取磁盘：文件缺失也不应报错，仅在实际下钻时报错。"""
        drill = DetailDrilldown(Path("not-exists.parquet"), ["department"], {"profit_amount": "profit_amt"})
        with self.assertRaises(FileNotFoundError):
            drill.top_by("department", "profit_amount")

    def test_top_by_aggregates_and_sorts(self) -> None:
        """top_by 应按维度求和并降序返回前 n 条。"""
        with tempfile.TemporaryDirectory(prefix="drilldown-") as temp:
            path = Path(temp) / "prepared.parquet"
            pd.DataFrame({
                "department": ["A", "A", "B"],
                "profit_amt": [10.0, 5.0, -3.0],
            }).to_parquet(path, index=False)
            drill = DetailDrilldown(path, ["department"], {"profit_amount": "profit_amt"})
            top = drill.top_by("department", "profit_amount", 2)
            self.assertEqual(top[0]["department"], "A")
            self.assertEqual(top[0]["profit_amount"], 15.0)
            self.assertEqual(drill.available_dimensions(), ["department"])
            self.assertEqual(drill.available_metrics(), ["profit_amount"])


if __name__ == "__main__":
    unittest.main()
