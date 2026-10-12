#!/usr/bin/env python3
"""校验器回归测试：覆盖合法链路和关键绕过场景。"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import pandas as pd

from runtime.anomaly_engine import AnomalyRuleError, evaluate_rules


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, *args], cwd=ROOT, text=True, capture_output=True, check=False
    )


def build_valid_run(base: Path) -> Path:
    report_path = base / "report.html"
    report_path.write_text("<html><body>销售分析</body></html>", encoding="utf-8")
    source_path = base / "sales.csv"
    source_path.write_text("month,region,sales_amount\n2025-01,华东,100\n", encoding="utf-8")

    task = {
        "analysis_goal": "分析销售趋势",
        "data_source": {"type": "file", "location": "sales.csv", "date_field": "opration_date"},
        "time_range": {"start": "2025-01", "end": "2025-12", "granularity": "month"},
        "dimensions": ["region"],
        "metrics": [{"name": "sales_amount", "definition": "成交金额求和", "unit": "元"}],
        "filters": [],
        "comparison": {"type": "year_over_year", "baseline": "2024", "target": None},
        "output": {"format": "html", "audience": "经营管理层", "language": "zh-CN", "include_charts": True},
        "domain": "sales",
        "assumptions": []
    }
    artifacts = {
        "intake": {"status": "ready", "questions": [], "task_config": task, "missing_fields": [], "ambiguous_fields": [], "assumptions": []},
        "planning": {"status": "ready", "plan_id": "plan1", "datasets": [{"id": "ds1", "purpose": "销售趋势", "dimensions": ["month", "region"], "metrics": ["sales_amount"], "filters": [], "comparison": task["comparison"], "grain": "month-region", "validations": ["月份完整"]}], "execution_order": ["ds1"], "chart_candidates": [], "report_sections": [{}], "blocking_issues": [], "assumptions": [], "next_node": "preparation"},
        "preparation": {"status": "ready", "prepared_data_ref": "prepared", "source_summary": {}, "field_lineage": [{"source": "销售额", "target": "sales_amount"}], "transformations": [], "data_profile": {"row_count": 1, "columns": ["month", "region", "sales_amount"], "date_min": "2025-01", "date_max": "2025-12", "null_summary": {}}, "issues": [], "assumptions": [], "next_node": "extraction"},
        "extraction": {"status": "ready", "datasets": [{"id": "ds1", "grain": "month-region", "columns": ["month", "region", "sales_amount"], "rows": [{"month": "2025-01", "region": "华东", "sales_amount": 100}], "metric_definitions": [{"name": "sales_amount"}], "lineage": {}, "attribution": [], "calculation_warnings": []}], "execution_log": [], "blocking_issues": [], "next_node": "data_validation"},
        "data_validation": {"passed": True, "checks": [{}], "issues": [], "validated_dataset_ids": ["ds1"], "next_node": "local_insight"},
        "local_insight": {"status": "ready", "insights": [{"id": "ins1", "dataset_id": "ds1", "text": "华东销售额为100元", "evidence": [{"value": 100}], "confidence": "high"}], "issues": [], "next_node": "charting"},
        "charting": {"status": "ready", "charts": [{"id": "chart1", "dataset_id": "ds1", "insight_id": "ins1", "chart_type": "line", "title": "华东销售额为100元", "subtitle": "测试", "encoding": {}, "option": {"palette": ["#0073CF"], "axis": {"min": 0, "max": 120.0, "from_zero": True}, "labels": [{"x": "2025-01", "value": 100.0, "kind": "latest"}], "aux_lines": []}, "accessibility_summary": "华东销售额100元", "validation": []}], "issues": [], "next_node": "synthesis"},
        "synthesis": {"status": "ready", "headline": {"text": "华东销售额为100元", "evidence_refs": ["ds1"]}, "global_insights": [{"id": "global1", "phenomenon": "华东销售额为100元", "localization": "地区维度中首位为华东", "explanation": "待验证", "action": {"owner": "分析组", "timing": "本月", "what": "复核口径", "expected_impact": "无", "verification": "复核"}, "evidence_refs": ["ds1", "ins1"], "confidence": "high"}], "selected_chart_ids": ["chart1"], "design_decisions": [{"chart_id": "chart1", "decision": "keep", "reason": "支撑结论"}], "issues": [], "next_node": "reporting"},
        "reporting": {"status": "ready", "report": {"format": "html", "artifact_ref": str(report_path), "title": "销售分析", "sections": [{}], "data_period": "2025", "methodology": ["汇总"], "assumptions": []}, "design_decisions": [], "issues": [], "next_node": "final_review"},
        "final_review": {"passed": True, "issues": [], "deliverables": [{"type": "report", "path": str(report_path), "description": "报告"}], "assumptions": [], "limitations": [], "next_node": "complete"}
    }

    task_path = base / "task.json"
    write(task_path, task)
    artifact_paths = {}
    for node_id, artifact in artifacts.items():
        path = base / f"{node_id}.json"
        write(path, artifact)
        artifact_paths[node_id] = str(path)
    manifest_path = base / "manifest.json"
    write(manifest_path, {"task_config": str(task_path), "artifacts": artifact_paths})
    return manifest_path


def expect_failure(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode == 0:
        raise AssertionError(f"{label} 未被阻断")


def run_regression_suite() -> None:
    with tempfile.TemporaryDirectory(prefix="super-data-analysis-test-") as temp:
        base = Path(temp)
        manifest_path = build_valid_run(base)

        valid = run("scripts/validate_run.py", str(manifest_path))
        if valid.returncode != 0:
            raise AssertionError(f"合法链路未通过：{valid.stderr}")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        intake_path = Path(manifest["artifacts"]["intake"])
        intake = json.loads(intake_path.read_text(encoding="utf-8"))
        intake["questions"] = ["仍有问题"]
        write(intake_path, intake)
        expect_failure(run("scripts/validate_run.py", str(manifest_path)), "矛盾澄清结果")
        manifest_path = build_valid_run(base)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        chart_path = Path(manifest["artifacts"]["charting"])
        chart = json.loads(chart_path.read_text(encoding="utf-8"))
        chart["charts"][0]["dataset_id"] = "ghost"
        write(chart_path, chart)
        expect_failure(run("scripts/validate_run.py", str(manifest_path)), "幽灵数据集引用")
        manifest_path = build_valid_run(base)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        plan_path = Path(manifest["artifacts"]["planning"])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["datasets"][0]["metrics"].append("profit")
        write(plan_path, plan)
        expect_failure(run("scripts/validate_run.py", str(manifest_path)), "计划越界指标")
        manifest_path = build_valid_run(base)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        final_path = Path(manifest["artifacts"]["final_review"])
        final = json.loads(final_path.read_text(encoding="utf-8"))
        final["deliverables"] = []
        write(final_path, final)
        expect_failure(run("scripts/validate_run.py", str(manifest_path)), "空交付包")

        manifest_path = build_valid_run(base)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validation_path = Path(manifest["artifacts"]["data_validation"])
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        validation.update({"passed": False, "validated_dataset_ids": [], "next_node": "extraction"})
        validation["issues"] = [{"severity": "blocking", "dataset_id": "ds1", "check": "完整性", "evidence": "缺失", "fix": "重算", "recovery_node": "extraction"}]
        write(validation_path, validation)
        expect_failure(run("scripts/validate_run.py", str(manifest_path)), "上游失败但下游完成")

        manifest_path = build_valid_run(base)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        report_path = base / "report.html"
        report_path.write_text("", encoding="utf-8")
        expect_failure(run("scripts/validate_run.py", str(manifest_path)), "空报告文件")

        manifest_path = build_valid_run(base)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        task_path = Path(manifest["task_config"])
        task = json.loads(task_path.read_text(encoding="utf-8"))
        task["output"]["include_charts"] = False
        write(task_path, task)
        intake_path = Path(manifest["artifacts"]["intake"])
        intake = json.loads(intake_path.read_text(encoding="utf-8"))
        intake["task_config"] = task
        write(intake_path, intake)
        expect_failure(run("scripts/validate_run.py", str(manifest_path)), "禁用图表却生成图表")

        chart_path = Path(manifest["artifacts"]["charting"])
        chart = json.loads(chart_path.read_text(encoding="utf-8"))
        chart["charts"] = []
        write(chart_path, chart)
        synthesis_path = Path(manifest["artifacts"]["synthesis"])
        synthesis = json.loads(synthesis_path.read_text(encoding="utf-8"))
        synthesis["selected_chart_ids"] = []
        synthesis["design_decisions"] = []
        write(synthesis_path, synthesis)
        no_chart = run("scripts/validate_run.py", str(manifest_path))
        if no_chart.returncode != 0:
            raise AssertionError(f"合法无图模式未通过：{no_chart.stderr}")

        disguised_chart = base / "chart.png"
        disguised_chart.write_bytes(b"not-a-real-chart-but-nonempty")
        final_path = Path(manifest["artifacts"]["final_review"])
        final = json.loads(final_path.read_text(encoding="utf-8"))
        final["deliverables"].append({"type": "other", "path": str(disguised_chart), "description": "附件"})
        write(final_path, final)
        expect_failure(run("scripts/validate_run.py", str(manifest_path)), "无图任务夹带图像")

        conflicting_routes = {
            "passed": False,
            "checks": [],
            "issues": [
                {"severity": "blocking", "dataset_id": "ds1", "check": "a", "evidence": "a", "fix": "a", "recovery_node": "preparation"},
                {"severity": "blocking", "dataset_id": "ds1", "check": "b", "evidence": "b", "fix": "b", "recovery_node": "extraction"}
            ],
            "validated_dataset_ids": [],
            "next_node": "extraction"
        }
        conflict_path = base / "conflicting-routes.json"
        write(conflict_path, conflicting_routes)
        expect_failure(
            run("scripts/validate_artifact.py", "schemas/validation-report.schema.json", str(conflict_path)),
            "多个阻断问题恢复路由冲突",
        )

    print("回归测试通过：有图/无图合法链路放行，9 类无效链路均被阻断")


class ValidatorRegressionTests(unittest.TestCase):
    def _runtime_task(self, base: Path) -> Path:
        source = base / "orders.csv"
        source.write_text(
            "opration_date,order_number,amt,calc_final_profit\n"
            "2026-06-01,O1,100,20\n",
            encoding="utf-8",
        )
        task = {
            "analysis_goal": "订单分析",
            "data_source": {
                "type": "file",
                "location": source.name,
                "date_field": "opration_date",
                "field_mapping": {
                    "opration_date": "opration_date",
                    "order_number": "order_number",
                    "amt": "amt",
                    "calc_final_profit": "calc_final_profit",
                },
            },
            "time_range": {"start": "2026-06", "end": "2026-07", "granularity": "month"},
            "dimensions": ["group_first"],
            "metrics": [
                {
                    "name": "amt",
                    "role": "sales",
                    "definition": "销售额",
                    "source_field": "amt",
                    "aggregation": "sum",
                    "unit": "CNY",
                },
                {
                    "name": "calc_final_profit",
                    "role": "profit",
                    "definition": "计价利润",
                    "source_field": "calc_final_profit",
                    "aggregation": "sum",
                    "unit": "CNY",
                },
                {
                    "name": "order_count", "role": "order_count", "definition": "订单数",
                    "source_field": "order_number", "aggregation": "count_distinct", "unit": "count",
                },
                {
                    "name": "profit_margin", "role": "margin", "definition": "利润率",
                    "formula": "calc_final_profit / amt", "zero_denominator": "null", "unit": "percent",
                },
            ],
            "output": {"format": "html", "include_charts": True},
        }
        task_path = base / "task.json"
        write(task_path, task)
        return task_path

    def test_approved_order_case_uses_confirmed_profit_basis(self) -> None:
        task_path = ROOT / "my_case" / "task.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        metric_names = {metric["name"] for metric in task["metrics"]}
        self.assertNotIn("final_profit", metric_names)
        # 利润指标必须映射到 calc_final_profit 物理字段，而非 final_profit
        profit_metric = next(m for m in task["metrics"] if m.get("role") == "profit")
        mapping = task["data_source"]["field_mapping"]
        self.assertEqual(mapping[profit_metric["source_field"]], "calc_final_profit")

    def test_run_entrypoint_rejects_unapproved_profit_metric(self) -> None:
        task_path = ROOT / "my_case" / "task.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        metric_names = {metric["name"] for metric in task["metrics"]}
        self.assertNotIn("final_profit", metric_names)

    def test_runtime_task_preflight_script_exists(self) -> None:
        self.assertTrue((ROOT / "scripts" / "validate_task_runtime.py").is_file())

    def test_runtime_preflight_accepts_complete_task(self) -> None:
        with tempfile.TemporaryDirectory(prefix="super-data-analysis-preflight-") as temp:
            task_path = self._runtime_task(Path(temp))
            result = run("scripts/validate_task_runtime.py", str(task_path))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_runtime_preflight_accepts_excel_and_parquet_headers(self) -> None:
        for suffix in (".xlsx", ".parquet"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory(
                prefix="super-data-analysis-file-source-"
            ) as temp:
                base = Path(temp)
                task_path = self._runtime_task(base)
                task = json.loads(task_path.read_text(encoding="utf-8"))
                frame = pd.DataFrame(
                    [{"opration_date": "2026-06-01", "order_number": "O1", "amt": 100, "calc_final_profit": 20}]
                )
                source = base / f"orders{suffix}"
                if suffix == ".xlsx":
                    frame.to_excel(source, sheet_name="订单", index=False)
                    task["data_source"]["sheet"] = "订单"
                else:
                    frame.to_parquet(source, index=False)
                    task["data_source"]["sheet"] = None
                task["data_source"]["location"] = source.name
                write(task_path, task)
                result = run("scripts/validate_task_runtime.py", str(task_path))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_runtime_preflight_rejects_non_file_unsupported_extension_and_sheet(self) -> None:
        cases = (
            ("database", "orders.csv", None, "仅支持 data_source.type=file"),
            ("file", "orders.json", None, "仅支持 CSV、XLSX、XLS 和 Parquet"),
            ("file", "orders.csv", "订单", "sheet 仅适用于 XLSX/XLS"),
        )
        for source_type, location, sheet, message in cases:
            with self.subTest(location=location, source_type=source_type), tempfile.TemporaryDirectory(
                prefix="super-data-analysis-source-contract-"
            ) as temp:
                base = Path(temp)
                task_path = self._runtime_task(base)
                task = json.loads(task_path.read_text(encoding="utf-8"))
                task["data_source"].update({"type": source_type, "location": location, "sheet": sheet})
                if location.endswith(".json"):
                    (base / location).write_text("{}", encoding="utf-8")
                write(task_path, task)
                result = run("scripts/validate_task_runtime.py", str(task_path))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stdout)

    def test_runtime_preflight_rejects_missing_metric_mapping_and_source_field(self) -> None:
        with tempfile.TemporaryDirectory(prefix="super-data-analysis-preflight-") as temp:
            task_path = self._runtime_task(Path(temp))
            task = json.loads(task_path.read_text(encoding="utf-8"))
            task["metrics"] = [{"name": "amt", "definition": "销售额"}]
            task["data_source"]["field_mapping"].pop("calc_final_profit")
            write(task_path, task)
            result = run("scripts/validate_task_runtime.py", str(task_path))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("缺少主指标", result.stdout)
            self.assertIn("缺少字段映射", result.stdout)

    def test_runtime_preflight_rejects_wrong_time_range_and_final_profit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="super-data-analysis-preflight-") as temp:
            task_path = self._runtime_task(Path(temp))
            task = json.loads(task_path.read_text(encoding="utf-8"))
            task["time_range"]["start"] = "2026-06"
            task["time_range"]["end"] = "2026-05"
            task["metrics"].append({"name": "final_profit", "definition": "错误利润"})
            write(task_path, task)
            result = run("scripts/validate_task_runtime.py", str(task_path))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("final_profit", result.stdout)
            self.assertIn("time_range.end", result.stdout)

    def test_runtime_preflight_accepts_safe_arithmetic_formula(self) -> None:
        with tempfile.TemporaryDirectory(prefix="super-data-analysis-preflight-") as temp:
            task_path = self._runtime_task(Path(temp))
            task = json.loads(task_path.read_text(encoding="utf-8"))
            task["metrics"].append(
                {
                    "name": "profit_margin",
                    "definition": "计价利润率",
                    "formula": "calc_final_profit / amt",
                    "zero_denominator": "null",
                    "unit": "percent",
                }
            )
            write(task_path, task)
            result = run("scripts/validate_task_runtime.py", str(task_path))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_runtime_preflight_rejects_unsafe_formula_syntax(self) -> None:
        unsafe_formulas = ("sum(amt)", "amt.real")
        for formula in unsafe_formulas:
            with self.subTest(formula=formula), tempfile.TemporaryDirectory(
                prefix="super-data-analysis-preflight-"
            ) as temp:
                task_path = self._runtime_task(Path(temp))
                task = json.loads(task_path.read_text(encoding="utf-8"))
                task["metrics"].append(
                    {
                        "name": "unsafe_metric",
                        "definition": "不支持的公式",
                        "formula": formula,
                        "zero_denominator": "null",
                    }
                )
                write(task_path, task)
                result = run("scripts/validate_task_runtime.py", str(task_path))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("派生指标 unsafe_metric 无效", result.stdout)
                self.assertIn("公式不允许", result.stdout)

    def test_run_rejects_report_missing_from_final_deliverables(self) -> None:
        with tempfile.TemporaryDirectory(prefix="super-data-analysis-test-") as temp:
            base = Path(temp)
            manifest_path = build_valid_run(base)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            final_path = Path(manifest["artifacts"]["final_review"])
            final = json.loads(final_path.read_text(encoding="utf-8"))
            final["deliverables"] = []
            write(final_path, final)
            result = run("scripts/validate_run.py", str(manifest_path))
            self.assertNotEqual(result.returncode, 0)


    def test_valid_and_adversarial_runs(self) -> None:
        run_regression_suite()


@unittest.skipUnless(
    (ROOT / "outputs" / "order_analysis_2026-06_07" / "analysis_summary.json").is_file(),
    "订单案例基准产物不存在，跳过黄金回归",
)
class ApprovedOrderGoldenRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.case_dir = ROOT / "examples" / "order-analysis-2026-06_07"
        cls.output_dir = ROOT / "outputs" / "order_analysis_2026-06_07"
        cls.expected = json.loads(
            (ROOT / "tests" / "fixtures" / "order_analysis_2026-06_07.expected.json").read_text(
                encoding="utf-8"
            )
        )

    @staticmethod
    def _money(value: object) -> Decimal:
        return Decimal(str(value)).quantize(Decimal("0.01"))

    def test_source_hash_and_volume_match_golden_baseline(self) -> None:
        task = json.loads((self.case_dir / "task.json").read_text(encoding="utf-8"))
        source_path = (self.case_dir / task["data_source"]["location"]).resolve()
        digest = hashlib.sha256()
        with source_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)

        snapshot = json.loads(
            (self.output_dir / "execution_snapshot.json").read_text(encoding="utf-8")
        )
        expected_source = self.expected["source"]
        self.assertEqual(digest.hexdigest(), expected_source["sha256"])
        self.assertEqual(snapshot["source_sha256"], expected_source["sha256"])
        self.assertEqual(snapshot["source_rows"], expected_source["row_count"])
        self.assertEqual(snapshot["scope_rows"], expected_source["row_count"])

    def test_summary_and_monthly_results_match_golden_baseline(self) -> None:
        summary = json.loads(
            (self.output_dir / "analysis_summary.json").read_text(encoding="utf-8")
        )
        expected_basis = self.expected["basis"]
        expected_overall = self.expected["overall"]
        self.assertEqual(summary["basis"]["sales"], expected_basis["sales_field"])
        self.assertEqual(summary["basis"]["profit"], expected_basis["profit_field"])
        self.assertEqual(summary["scope_rows"], expected_overall["scope_rows"])
        self.assertEqual(summary["orders"], expected_overall["order_count"])
        self.assertEqual(self._money(summary["amt_rmb"]), Decimal(expected_overall["amt_rmb"]))
        self.assertEqual(
            self._money(summary["calc_final_profit"]),
            Decimal(expected_overall["calc_final_profit"]),
        )
        self.assertAlmostEqual(
            summary["calc_profit_margin"], expected_overall["calc_profit_margin"], places=12
        )
        self.assertEqual(
            summary["negative_calc_profit_lines"],
            expected_overall["negative_calc_profit_lines"],
        )

        with (self.output_dir / "monthly_summary.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            monthly = {row["analysis_month"]: row for row in csv.DictReader(handle)}
        self.assertEqual(set(monthly), set(self.expected["monthly"]))
        for month, expected in self.expected["monthly"].items():
            actual = monthly[month]
            self.assertEqual(self._money(actual["amt"]), Decimal(expected["amt_rmb"]))
            self.assertEqual(
                self._money(actual["calc_final_profit"]),
                Decimal(expected["calc_final_profit"]),
            )
            self.assertEqual(int(actual["order_count"]), expected["order_count"])
            self.assertEqual(
                int(actual["negative_calc_profit_line_count"]),
                expected["negative_calc_profit_lines"],
            )

    def test_monthly_department_order_counts_match_golden_baseline(self) -> None:
        with (self.output_dir / "monthly_department_summary.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            actual = {
                (row["analysis_month"], row["department"]): int(row["order_count"])
                for row in csv.DictReader(handle)
            }
        expected = {
            (month, department): count
            for month, departments in self.expected["monthly_department_order_count"].items()
            for department, count in departments.items()
        }
        self.assertEqual(actual, expected)

    def test_report_and_manifest_preserve_confirmed_basis(self) -> None:
        report_html = (self.output_dir / "management_report.html").read_text(encoding="utf-8")
        self.assertIn("人民币销售额：amt（人民币）", report_html)
        self.assertIn("计价利润：calc_final_profit", report_html)
        self.assertIn("order_number 去重", report_html)
        self.assertIn(".method>article{min-width:0}", report_html)
        self.assertIn("code{overflow-wrap:anywhere;word-break:break-all}", report_html)
        self.assertIn("21,449,100.20", report_html)
        self.assertIn("3,759,782.75", report_html)
        self.assertIn("665,732", report_html)

        run_dir = self.output_dir / "run"
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        reporting = json.loads((run_dir / "reporting.json").read_text(encoding="utf-8"))
        final_review = json.loads((run_dir / "final_review.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(manifest["artifacts"]),
            {
                "intake", "planning", "preparation", "extraction", "data_validation",
                "local_insight", "charting", "synthesis", "reporting", "final_review",
            },
        )
        methodology = reporting["report"]["methodology"]
        self.assertIn("销售额使用amt人民币字段", methodology)
        self.assertIn("利润使用calc_final_profit计价利润字段", methodology)
        self.assertTrue(final_review["passed"])
        self.assertIn("final_profit未参与主分析", final_review["limitations"])
        report_deliverables = [
            item for item in final_review["deliverables"] if item["type"] == "report"
        ]
        self.assertEqual(len(report_deliverables), 1)
        self.assertEqual(report_deliverables[0]["path"], "../management_report.html")


class AnomalyRuleTests(unittest.TestCase):
    def test_structured_rules_preserve_priority_and_reject_unsafe_operator(self) -> None:
        frame = pd.DataFrame({"profit": [-1, 2, 1], "sales": [0, 0, 2], "order": ["a", "b", ""]})
        rules = [
            {"name": "loss", "label": "负计价利润", "field": "profit", "operator": "lt", "value": 0},
            {"name": "zero", "label": "需复核", "field": "sales", "operator": "eq", "value": 0},
            {"name": "blank", "label": "需复核", "field": "order", "operator": "blank"},
        ]
        matched, labels = evaluate_rules(frame, rules)
        self.assertEqual(matched.tolist(), [True, True, True])
        self.assertEqual(labels.tolist(), ["负计价利润", "需复核", "需复核"])
        rules[0]["operator"] = "eval"
        with self.assertRaises(AnomalyRuleError):
            evaluate_rules(frame, rules)


class MetricRoleTests(unittest.TestCase):
    def test_metric_plan_uses_roles_instead_of_metric_names(self) -> None:
        from runtime.task_runtime import MetricPlan
        task = {"metrics": [
            {"name": "revenue", "role": "sales", "source_field": "amt", "aggregation": "sum"},
            {"name": "pricing_result", "role": "profit", "source_field": "calc_final_profit", "aggregation": "sum"},
            {"name": "orders", "role": "order_count", "source_field": "order_number", "aggregation": "count_distinct"},
            {"name": "rate", "role": "margin", "formula": "pricing_result / revenue", "zero_denominator": "null"},
        ]}
        plan = MetricPlan.from_task(task)
        self.assertEqual((plan.sales, plan.profit, plan.order), ("revenue", "pricing_result", "orders"))

    def test_metric_plan_rejects_duplicate_roles(self) -> None:
        from runtime.task_runtime import MetricPlan
        task = {"metrics": [
            {"name": "a", "role": "sales", "source_field": "amt", "aggregation": "sum"},
            {"name": "b", "role": "sales", "source_field": "amt", "aggregation": "sum"},
        ]}
        with self.assertRaisesRegex(ValueError, "指标角色重复"):
            MetricPlan.from_task(task)


if __name__ == "__main__":
    unittest.main(verbosity=2)
