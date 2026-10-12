"""可插拔报告模板测试：Markdown 与 XLSX 渲染的正确性。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.generate_html_report import _render_markdown, _render_xlsx


def _summary() -> dict:
    """构造一个最小可用的 summary 字典。"""
    return {
        "sales_amount": 123456.78,
        "pricing_profit": 23456.78,
        "profit_margin": 0.19,
        "orders": 1000,
        "scope_rows": 5000,
        "negative_pricing_profit_lines": 120,
        "negative_pricing_profit_line_rate": 0.024,
    }


class ReportWriterTests(unittest.TestCase):
    """验证 Markdown 与 XLSX 渲染器输出。"""

    def test_render_markdown_contains_key_sections(self) -> None:
        """Markdown 报告应包含标题、KPI 概览和主要结论。"""
        monthly = pd.DataFrame([{"analysis_month": "2026-06", "amt": 100.0, "calc_final_profit": 10.0}])
        departments = pd.DataFrame([{"department": "部门A", "amt": 100.0, "calc_final_profit": 10.0}])
        md = _render_markdown(
            report_title="测试报告", report_subtitle="自动生成", audience="管理层",
            start="2026年06月01日", end="2026年07月31日",
            summary=_summary(),
            comparison={"baseline": "2026-06", "target": "2026-07", "sales_growth": 0.1, "profit_growth": 0.2},
            sales_label="销售额", profit_label="利润", margin_label="利润率", order_label="订单数",
            monthly=monthly, departments=departments, primary_entity_label="部门",
            anomaly_count=10, generated_at="2026-10-07 00:00",
        )
        self.assertIn("# 测试报告", md)
        self.assertIn("## 经营概览", md)
        self.assertIn("## 主要结论", md)
        self.assertIn("销售额", md)
        self.assertIn("## 月度表现", md)
        self.assertIn("## 部门表现", md)

    def test_render_xlsx_creates_sheets(self) -> None:
        """XLSX 报告应生成文件并包含概览、月度、主实体 sheet。"""
        monthly = pd.DataFrame([{"analysis_month": "2026-06", "amt": 100.0}])
        departments = pd.DataFrame([{"department": "部门A", "amt": 100.0}])
        with tempfile.TemporaryDirectory(prefix="report-xlsx-") as temp:
            out = Path(temp)
            path = _render_xlsx(out, _summary(), monthly, departments, anomaly_count=5)
            self.assertTrue(path.exists())
            with pd.ExcelFile(path) as xls:
                sheets = xls.sheet_names
            self.assertIn("概览", sheets)
            self.assertIn("月度汇总", sheets)
            self.assertIn("主实体汇总", sheets)


if __name__ == "__main__":
    unittest.main()
