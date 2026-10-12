"""真实订单产物勾稽与配置驱动运行时边界测试。"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "my_case"
OUTPUT_DIR = ROOT / "outputs" / "order_analysis_2026-06_07"
EXPECTED_PATH = ROOT / "tests" / "fixtures" / "order_analysis_2026-06_07.expected.json"


def money(value: object) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


class RuntimeBoundaryTests(unittest.TestCase):
    def test_metric_aggregator_custom_primary_dimension(self) -> None:
        """验证 MetricAggregator 使用自定义 primary_dimension（非 department）仍能正确产出交叉汇总。"""
        sys.path.insert(0, str(ROOT))
        from runtime.metric_engine import MetricAggregator
        from runtime.task_runtime import MetricPlan

        task = {
            "metrics": [
                {"name": "rev", "role": "sales", "label": "营收", "definition": "营收", "source_field": "rev_src", "aggregation": "sum", "unit": "元"},
                {"name": "profit_x", "role": "profit", "label": "利润", "definition": "利润", "source_field": "profit_src", "aggregation": "sum", "unit": "元"},
                {"name": "ord", "role": "order_count", "label": "订单数", "definition": "订单数", "source_field": "oid", "aggregation": "count_distinct"},
                {"name": "pm", "role": "margin", "label": "利润率", "definition": "利润率", "formula": "profit_x / rev", "zero_denominator": "null"},
            ]
        }
        plan = MetricPlan.from_task(task)
        dimensions = ["region", "product"]
        aggregator = MetricAggregator(plan, dimensions, primary_dimension="region", profit_source_field="profit_src")

        chunk = pd.DataFrame({
            "analysis_month": ["2026-01", "2026-01", "2026-02"],
            "region": ["华东", "华北", "华东"],
            "product": ["P1", "P1", "P2"],
            "oid": ["O1", "O2", "O1"],
            "rev_src": [100.0, 200.0, 150.0],
            "profit_src": [10.0, -5.0, 30.0],
            "line_count": [1, 1, 1],
        })
        aggregator.consume(chunk)
        summaries, monthly_primary = aggregator.summaries()
        self.assertIsNotNone(monthly_primary)
        # 交叉汇总键应为 analysis_month + region（自定义 primary_dimension）
        self.assertIn("region", monthly_primary.columns)
        # 订单数应按 (month, region) 去重
        jan_east = monthly_primary[(monthly_primary["analysis_month"] == "2026-01") & (monthly_primary["region"] == "华东")]
        self.assertEqual(int(jan_east["ord"].iloc[0]), 1)
        # 动态 profit 字段：profit_src < 0 的行数
        region_summary = summaries["region"]
        north = region_summary[region_summary["region"] == "华北"]
        self.assertEqual(int(north["negative_profit_line_count"].iloc[0]), 1)
        # 兼容历史列名
        self.assertIn("negative_calc_profit_line_count", region_summary.columns)
        self.assertEqual(int(north["negative_calc_profit_line_count"].iloc[0]), 1)

    def test_metric_aggregator_without_primary_omits_cross_summary(self) -> None:
        """验证未传 primary_dimension 时，交叉汇总返回 None，但单维度 summaries 仍完整。"""
        sys.path.insert(0, str(ROOT))
        from runtime.metric_engine import MetricAggregator
        from runtime.task_runtime import MetricPlan

        task = {
            "metrics": [
                {"name": "amt2", "role": "sales", "label": "销售", "definition": "销售", "source_field": "amt2", "aggregation": "sum"},
                {"name": "pf2", "role": "profit", "label": "利润", "definition": "利润", "source_field": "pf2", "aggregation": "sum"},
                {"name": "oc2", "role": "order_count", "label": "单量", "definition": "单量", "source_field": "oid2", "aggregation": "count_distinct"},
                {"name": "mg2", "role": "margin", "label": "利润率", "definition": "利润率", "formula": "pf2 / amt2", "zero_denominator": "null"},
            ]
        }
        plan = MetricPlan.from_task(task)
        aggregator = MetricAggregator(plan, dimensions=["channel"], primary_dimension=None)
        chunk = pd.DataFrame({
            "analysis_month": ["2026-03", "2026-03"],
            "channel": ["线上", "线下"],
            "oid2": ["A", "B"],
            "amt2": [50.0, 80.0],
            "pf2": [5.0, -4.0],
            "line_count": [1, 1],
        })
        aggregator.consume(chunk)
        summaries, monthly_primary = aggregator.summaries()
        self.assertIsNone(monthly_primary)
        self.assertIn("channel", summaries)
        self.assertIn("analysis_month", summaries)
        channel_df = summaries["channel"]
        offline = channel_df[channel_df["channel"] == "线下"]
        # 负利润行计数基于 pf2 动态计算
        self.assertEqual(int(offline["negative_profit_line_count"].iloc[0]), 1)

    @unittest.skipUnless(
        (OUTPUT_DIR / "analysis_summary.json").is_file(),
        "订单案例基准产物不存在，跳过运行清单元数据测试",
    )
    def test_run_manifest_uses_task_report_metadata_and_metric_labels(self) -> None:
        task = json.loads((CASE_DIR / "task.json").read_text(encoding="utf-8"))
        task["output"]["title"] = "配置化经营报告"
        task["metrics"][0]["label"] = "配置销售额"
        task["metrics"][1]["label"] = "配置利润"
        with tempfile.TemporaryDirectory(prefix="dynamic-run-manifest-") as temp:
            temp_path = Path(temp)
            task_path = temp_path / "task.json"
            task_path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
            for name in (
                "monthly_summary.csv",
                "monthly_department_summary.csv",
                "analysis_summary.json",
                "execution_snapshot.json",
            ):
                shutil.copyfile(OUTPUT_DIR / name, temp_path / name)
            sys.path.insert(0, str(ROOT))
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from scripts.build_approved_run import build_run
            from local_agent_hooks import SampleAgentHooks
            # 注入本地 Agent 推理实现：推理节点由本地 Agent 产出，验证链路可跑通。
            build_run(task_path, temp_path, SampleAgentHooks())
            reporting = json.loads((temp_path / "run" / "reporting.json").read_text(encoding="utf-8"))
            charting = json.loads((temp_path / "run" / "charting.json").read_text(encoding="utf-8"))
            self.assertEqual(reporting["report"]["title"], "配置化经营报告")
            self.assertEqual(charting["charts"][0]["title"], "月度配置销售额与配置利润")
            self.assertEqual(charting["charts"][1]["title"], "部门配置利润")

    def test_runtime_modules_import_independently(self) -> None:
        command = (
            "import runtime.anomaly_engine, runtime.metric_engine, "
            "runtime.safe_formula, runtime.source_reader, runtime.task_runtime"
        )
        result = subprocess.run(
            [sys.executable, "-c", command],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_anomaly_rules_are_configuration_driven(self) -> None:
        from runtime.anomaly_engine import evaluate_rules

        frame = pd.DataFrame(
            {
                "amt": [100, 0, 50],
                "calc_final_profit": [-1, 0, 2],
                "order_number": ["A", "B", ""],
            }
        )
        rules = [
            {"name": "zero_amt", "label": "零销售", "field": "amt", "operator": "eq", "value": 0},
            {"name": "blank_order", "label": "缺订单", "field": "order_number", "operator": "blank"},
        ]
        matched, labels = evaluate_rules(frame, rules)
        self.assertEqual(matched.tolist(), [False, True, True])
        self.assertEqual(labels.tolist(), ["", "零销售", "缺订单"])

        changed_rules = [
            {"name": "loss", "label": "亏损", "field": "calc_final_profit", "operator": "lt", "value": 0}
        ]
        changed, changed_labels = evaluate_rules(frame, changed_rules)
        self.assertEqual(changed.tolist(), [True, False, False])
        self.assertEqual(changed_labels.tolist(), ["亏损", "", ""])

    def test_runtime_preflight_rejects_final_profit_source(self) -> None:
        task = json.loads((CASE_DIR / "task.json").read_text(encoding="utf-8"))
        task["metrics"].append(
            {
                "name": "legacy_profit",
                "definition": "禁止的旧利润口径",
                "source_field": "final_profit",
                "aggregation": "sum",
                "unit": "CNY",
            }
        )
        task["data_source"]["field_mapping"]["final_profit"] = "final_profit"
        with tempfile.TemporaryDirectory(prefix="final-profit-preflight-") as temp:
            task_path = Path(temp) / "task.json"
            task["data_source"]["location"] = str(
                (ROOT / "temp" / "三部门订单原始明细_2026年6-7月.csv").resolve()
            )
            task_path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/validate_task_runtime.py", str(task_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("final_profit 不允许作为任何主指标源字段", result.stdout)

    def test_summary_comparison_metadata_uses_actual_periods(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        from generate_html_report import build_summary

        task = {"output": {"audience": "测试受众"}}
        snapshot = {
            "basis": {"sales": "amt", "profit": "calc_final_profit"},
            "scope_rows": 2,
            "analysis_period": {"start": "2025-01-01", "end_exclusive": "2025-03-01"},
        }
        monthly = pd.DataFrame(
            [
                {"analysis_month": "2025-01", "amt": 100, "calc_final_profit": 10, "order_count": 1, "negative_calc_profit_line_count": 0},
                {"analysis_month": "2025-02", "amt": 120, "calc_final_profit": 18, "order_count": 1, "negative_calc_profit_line_count": 0},
            ]
        )
        departments = pd.DataFrame(
            [{"department": "测试部", "amt": 220, "calc_final_profit": 28, "line_count": 2, "negative_calc_profit_line_count": 0}]
        )
        summary = build_summary(task, snapshot, monthly, departments, anomaly_count=0)
        self.assertEqual(summary["period_comparison"]["baseline"], "2025-01")
        self.assertEqual(summary["period_comparison"]["target"], "2025-02")
        self.assertNotIn("july_vs_june", summary)


@unittest.skipUnless(
    (OUTPUT_DIR / "run" / "manifest.json").is_file(),
    "订单案例基准产物不存在，跳过交付物勾稽",
)
class DeliverableReconciliationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
        manifest = json.loads((OUTPUT_DIR / "run" / "manifest.json").read_text(encoding="utf-8"))
        final_review = json.loads((OUTPUT_DIR / "run" / "final_review.json").read_text(encoding="utf-8"))
        dataset_item = next(item for item in final_review["deliverables"] if item["type"] == "dataset")
        cls.parquet_path = (OUTPUT_DIR / "run" / dataset_item["path"]).resolve()
        cls.manifest = manifest
        cls.final_review = final_review

    def test_parquet_reconciles_to_golden_and_monthly_outputs(self) -> None:
        self.assertTrue(self.parquet_path.is_file())
        metadata = pq.ParquetFile(self.parquet_path).metadata
        self.assertEqual(metadata.num_rows, self.expected["source"]["row_count"])
        frame = pq.read_table(
            self.parquet_path,
            columns=["order_number", "analysis_month", "amt", "calc_final_profit"],
        ).to_pandas()
        overall = self.expected["overall"]
        self.assertEqual(frame["order_number"].nunique(), overall["order_count"])
        self.assertEqual(money(frame["amt"].sum()), Decimal(overall["amt_rmb"]))
        self.assertEqual(money(frame["calc_final_profit"].sum()), Decimal(overall["calc_final_profit"]))
        self.assertEqual(int(frame["calc_final_profit"].lt(0).sum()), overall["negative_calc_profit_lines"])

        for month, expected in self.expected["monthly"].items():
            selected = frame.loc[frame["analysis_month"].eq(month)]
            self.assertEqual(money(selected["amt"].sum()), Decimal(expected["amt_rmb"]))
            self.assertEqual(money(selected["calc_final_profit"].sum()), Decimal(expected["calc_final_profit"]))
            self.assertEqual(selected["order_number"].nunique(), expected["order_count"])

    def test_anomaly_output_matches_configured_rule_union(self) -> None:
        anomalies = pd.read_csv(OUTPUT_DIR / "anomaly_orders.csv", encoding="utf-8-sig")
        expected = self.expected["anomalies"]
        profits = pd.to_numeric(anomalies["calc_final_profit"], errors="coerce")
        amounts = pd.to_numeric(anomalies["amt"], errors="coerce")
        blank_orders = anomalies["order_number"].fillna("").astype(str).str.strip().eq("")
        self.assertEqual(len(anomalies), expected["row_count"])
        self.assertEqual(int(profits.lt(0).sum()), expected["negative_profit_rows"])
        self.assertEqual(int(amounts.eq(0).sum()), expected["zero_amt_rows"])
        self.assertEqual(int(blank_orders.sum()), expected["blank_order_rows"])
        self.assertEqual(anomalies["anomaly_types"].value_counts().to_dict(), expected["labels"])
        self.assertFalse({"final_profit", "total_profit"} & set(anomalies.columns))

    def test_manifest_deliverables_and_basis_are_consistent(self) -> None:
        reporting = json.loads((OUTPUT_DIR / "run" / "reporting.json").read_text(encoding="utf-8"))
        snapshot = json.loads((OUTPUT_DIR / "execution_snapshot.json").read_text(encoding="utf-8"))
        summary = json.loads((OUTPUT_DIR / "analysis_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["basis"]["sales"], "amt")
        self.assertEqual(snapshot["basis"]["profit"], "calc_final_profit")
        self.assertEqual(summary["basis"]["sales"], "amt")
        self.assertEqual(summary["basis"]["profit"], "calc_final_profit")
        methodology = " ".join(reporting["report"]["methodology"])
        self.assertIn("amt", methodology)
        self.assertIn("calc_final_profit", methodology)
        self.assertIn("final_profit不参与分析", methodology)
        for item in self.final_review["deliverables"]:
            self.assertTrue((OUTPUT_DIR / "run" / item["path"]).resolve().is_file())

    def test_only_period_named_parquet_is_delivered(self) -> None:
        expected_name = "prepared_orders_2026_06_2026_07.parquet"
        parquet_files = sorted(path.name for path in OUTPUT_DIR.glob("prepared_orders*.parquet"))
        self.assertEqual(parquet_files, [expected_name])
        dataset_item = next(
            item for item in self.final_review["deliverables"] if item["type"] == "dataset"
        )
        self.assertEqual(Path(dataset_item["path"]).name, expected_name)
        preparation = json.loads(
            (OUTPUT_DIR / "run" / "preparation.json").read_text(encoding="utf-8")
        )
        self.assertEqual(Path(preparation["prepared_data_ref"]).name, expected_name)
        self.assertFalse((OUTPUT_DIR / "prepared_orders_2026-06_07.parquet").exists())
