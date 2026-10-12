"""报告布局与规范（KPI 数量、图表剔除、容器尺寸）的单元测试。"""

from __future__ import annotations

import unittest

from runtime import report_style


def candidate(cid: str, dimension: str, period: str, values: list[float]) -> dict:
    return {"id": cid, "dimension": dimension, "period": period, "values": values}


class ReportStyleTests(unittest.TestCase):
    """验证报告规范被正确编码为可复用规则。"""

    def test_prune_removes_duplicate_dimension_and_period(self) -> None:
        """同维度、同时间范围的图表应被剔除（结论重复）。"""
        kept, removed = report_style.prune_charts([
            candidate("a", "department", "P", [100.0, 40.0]),
            candidate("b", "department", "P", [10.0, 5.0]),
        ])
        self.assertEqual([c["id"] for c in kept], ["a"])
        self.assertEqual(removed[0]["id"], "b")
        self.assertIn("重复", removed[0]["reason"])

    def test_prune_removes_low_difference(self) -> None:
        """组间差异 < 5% 视为无有效信息。"""
        kept, removed = report_style.prune_charts([candidate("a", "channel", "P", [100.0, 98.0])])
        self.assertEqual(kept, [])
        self.assertIn("无有效信息", removed[0]["reason"])

    def test_prune_caps_charts_per_tab(self) -> None:
        """每 Tab 最多 4 张图，超出剔除。"""
        items = [candidate(f"c{i}", f"dim{i}", "P", [100.0, i]) for i in range(6)]
        kept, removed = report_style.prune_charts(items)
        self.assertEqual(len(kept), 4)
        self.assertEqual(len(removed), 2)
        self.assertIn("最多 4 张", removed[0]["reason"])

    def test_container_height_rules(self) -> None:
        """双轴 320（最小 280）；折线 260/300；横向条形每项 40px。"""
        self.assertEqual(report_style.container_height("dual_axis"), 320)
        self.assertEqual(report_style.container_height("line", points=2), 260)
        self.assertEqual(report_style.container_height("line", points=48), 300)
        self.assertEqual(report_style.container_height("horizontal_bar", items=8), 320)
        self.assertIsNone(report_style.container_height("vertical_bar", items=3))

    def test_note_styles_match_spec(self) -> None:
        """备注字体：来源 11px#888、口径 11px#666、结论卡片 12px#444。"""
        self.assertEqual(report_style.note_style("source"), {"font_size": 11, "color": "#888"})
        self.assertEqual(report_style.note_style("methodology"), {"font_size": 11, "color": "#666"})
        self.assertEqual(
            report_style.note_style("chart_conclusion"),
            {"font_size": 12, "color": "#444", "accent": "#003366"},
        )

    def test_kpi_and_chart_limits(self) -> None:
        """KPI 数量为 5，每屏两列，每 Tab 最多 4 张图。"""
        cfg = report_style.load_report()
        self.assertEqual(cfg.get("kpi_count"), 5)
        self.assertEqual(cfg.get("max_charts_per_tab"), 4)
        self.assertEqual(cfg.get("chart_columns"), "6:4")


if __name__ == "__main__":
    unittest.main()
