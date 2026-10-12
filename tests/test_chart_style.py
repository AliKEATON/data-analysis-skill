"""图表规范（配色/坐标轴/标注/辅助线/选型）的单元测试。"""

from __future__ import annotations

import unittest

from runtime import chart_style


class ChartStyleTests(unittest.TestCase):
    """验证图表规范被正确编码为可复用规则。"""

    def test_palette_only_contains_allowed_colors(self) -> None:
        """调色板必须严格等于规范给定的六个颜色。"""
        self.assertEqual(
            chart_style.allowed_colors(),
            {"#003366", "#0073CF", "#5AB4E0", "#E63329", "#F7941D", "#00A551"},
        )

    def test_choose_chart_type_rules(self) -> None:
        """时序→line；类别≤5→vertical_bar；类别≥6→horizontal_bar。"""
        self.assertEqual(chart_style.choose_chart_type(3, has_time_series=True), "line")
        self.assertEqual(chart_style.choose_chart_type(5), "vertical_bar")
        self.assertEqual(chart_style.choose_chart_type(6), "horizontal_bar")
        self.assertEqual(chart_style.choose_chart_type(3, dual_axis=True), "dual_axis")

    def test_axis_rules(self) -> None:
        """Y 轴最大值 = 数据最大值 × 1.2；柱状图从 0 开始。"""
        self.assertAlmostEqual(chart_style.y_axis_max([10.0, 20.0, 5.0]), 24.0)
        bounds = chart_style.axis_bounds([10.0, 20.0, 5.0], from_zero=True)
        self.assertEqual(bounds["min"], 0.0)
        self.assertAlmostEqual(bounds["max"], 24.0)
        self.assertTrue(bounds["from_zero"])
        # 折线图压缩起点时必须标注「非零起点」
        compressed = chart_style.axis_bounds([100.0, 120.0], from_zero=False)
        self.assertEqual(compressed["note"], "非零起点")

    def test_select_label_points_at_most_three(self) -> None:
        """最多标注 3 个关键点：最新值、峰值、对比基准。"""
        points = [{"x": "1", "value": 5.0}, {"x": "2", "value": 9.0}, {"x": "3", "value": 7.0}, {"x": "4", "value": 6.0}]
        picked = chart_style.select_label_points(points)
        self.assertLessEqual(len(picked), 3)
        kinds = {item["kind"] for item in picked}
        self.assertIn("latest", kinds)
        self.assertIn("peak", kinds)
        self.assertIn("baseline", kinds)
        self.assertEqual(next(i for i in picked if i["kind"] == "latest")["x"], "4")
        self.assertEqual(next(i for i in picked if i["kind"] == "peak")["x"], "2")

    def test_labels_closed_when_points_over_threshold(self) -> None:
        """数据点超过 20 时关闭逐点标注。"""
        points = [{"x": str(i), "value": float(i)} for i in range(25)]
        self.assertEqual(chart_style.select_label_points(points), [])

    def test_aux_line_colors_and_styles(self) -> None:
        """均值线为橙色虚线；目标线为红色点线，且均带标签。"""
        lines = chart_style.aux_lines(mean=12.3, target=15.0)
        average = next(line for line in lines if line["kind"] == "average")
        target = next(line for line in lines if line["kind"] == "target")
        self.assertEqual((average["color"], average["style"]), ("#F7941D", "dashed"))
        self.assertEqual((target["color"], target["style"]), ("#E63329", "dotted"))
        self.assertTrue(average["label"].startswith("均值"))
        self.assertTrue(target["label"].startswith("目标"))

    def test_build_and_validate_option(self) -> None:
        """build_option 产出的 option 合规；出现规范外颜色时 validate_option 报错。"""
        option = chart_style.build_option([10.0, 20.0], label_points=[{"x": "a", "value": 10.0}], mean=15.0)
        self.assertEqual(chart_style.validate_option(option), [])
        bad = {"palette": ["#FF0000"], "axis": {"min": 0, "max": 1, "from_zero": True}, "labels": [], "aux_lines": []}
        self.assertTrue(chart_style.validate_option(bad))


if __name__ == "__main__":
    unittest.main()
