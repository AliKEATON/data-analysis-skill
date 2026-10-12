"""通用 execute_pipeline 端到端回归测试：订单 task.json 不改字段跑通且金额一致。"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "my_case"
EXPECTED_PATH = ROOT / "tests" / "fixtures" / "order_analysis_2026-06_07.expected.json"
TEMP_CSV = ROOT / "temp" / "三部门订单原始明细_2026年6-7月.csv"


def money(value: object) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


class ExecutePipelineTests(unittest.TestCase):
    def test_execute_pipeline_on_order_case_matches_golden_overall(self) -> None:
        """在临时目录复制三部门CSV和task.json，执行新流水线，断言overall金额一致、兼容文件存在。"""
        expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
        self.assertTrue(TEMP_CSV.is_file(), f"原始CSV不存在：{TEMP_CSV}")

        with tempfile.TemporaryDirectory(prefix="execute-pipeline-order-case-") as temp:
            temp_path = Path(temp)
            # 复制 task.json 和 CSV，保持相对路径
            case_temp = temp_path / "case"
            case_temp.mkdir()
            temp_csv_dir = temp_path / "temp"
            temp_csv_dir.mkdir()
            src_task = CASE_DIR / "task.json"
            task = json.loads(src_task.read_text(encoding="utf-8"))
            # 将 task 中 CSV 的相对路径改成相对于新 case 目录
            csv_rel_to_case = Path(task["data_source"]["location"])
            new_csv = temp_csv_dir / csv_rel_to_case.name
            shutil.copyfile(TEMP_CSV, new_csv)
            task["data_source"]["location"] = f"../temp/{csv_rel_to_case.name}"
            new_task = case_temp / "task.json"
            new_task.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")

            output_dir = temp_path / "out"
            output_dir.mkdir()

            import sys
            sys.path.insert(0, str(ROOT))
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from scripts.execute_pipeline import run
            from local_agent_hooks import SampleAgentHooks
            # 注入本地 Agent 推理实现：推理节点由本地 Agent 产出，不再走模板。
            run(new_task, output_dir, hooks=SampleAgentHooks())

            # 查找 prepared_*.parquet
            prepared_candidates = sorted(output_dir.glob("prepared_*.parquet"))
            self.assertTrue(prepared_candidates, "未生成 prepared parquet 文件")
            prepared_path = prepared_candidates[0]
            frame = pq.read_table(
                prepared_path,
                columns=["order_number", "analysis_month", "sales_amt", "profit_amt"],
            ).to_pandas()
            overall = expected["overall"]
            self.assertEqual(frame["order_number"].nunique(), overall["order_count"])
            # 保留 2 位小数比较
            self.assertEqual(money(frame["sales_amt"].sum()), Decimal(overall["amt_rmb"]))
            self.assertEqual(money(frame["profit_amt"].sum()), Decimal(overall["calc_final_profit"]))

            # 兼容文件名存在断言：旧名 department_summary.csv 或新名 dimension_department.csv 至少其一
            dept_old = output_dir / "department_summary.csv"
            dept_new = output_dir / "dimension_department.csv"
            self.assertTrue(
                dept_old.exists() or dept_new.exists(),
                f"主维度文件缺失：{dept_old} / {dept_new}",
            )
            # 第二维度 group → dimension_group.csv
            self.assertTrue(
                (output_dir / "dimension_group.csv").exists(),
                "维度文件缺失：dimension_group.csv",
            )


if __name__ == "__main__":
    unittest.main()
