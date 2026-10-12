from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
import pandas as pd
from runtime.source_reader import SourceReadError, read_scoped_file, apply_derived_dimensions


class SourceReaderTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({
            "日期": ["2026-05-31 23:59:59", "2026-06-01", "2026-07-31 23:59:59", "2026-08-01"],
            "订单": ["OUT-BEFORE", " A ", "B", "OUT-AFTER"],
            "金额": ["9", "￥1,200.50", "300", "5"],
            "利润": ["1", "120.25", "-30", "2"],
            "部门名称": ["其他", " 华东 ", "华南", "其他"],
            "未映射字段": ["a", "b", "c", "d"],
        })
        self.mapping = {
            "opration_date": "日期", "order_number": "订单", "amt": "金额",
            "calc_final_profit": "利润", "department": "部门名称",
        }
        self.start, self.end = pd.Timestamp("2026-06-01"), pd.Timestamp("2026-08-01")

    def _read(self, path, sheet=None, date_logical="opration_date", derived=None):
        return list(read_scoped_file(path, {"field_mapping": self.mapping, "sheet": sheet}, {"amt", "calc_final_profit"}, self.start, self.end, 1, date_logical, derived))

    def test_csv_xlsx_and_parquet_share_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); paths = [base/"x.csv", base/"x.xlsx", base/"x.parquet"]
            self.frame.to_csv(paths[0], index=False, encoding="utf-8-sig")
            self.frame.to_excel(paths[1], index=False, sheet_name="Data")
            self.frame.to_parquet(paths[2], index=False)
            normalized = {}
            for path in paths:
                with self.subTest(path.suffix):
                    chunks = self._read(path, "Data" if path.suffix == ".xlsx" else None)
                    self.assertEqual(sum(raw for raw, _ in chunks), 4)
                    scoped = pd.concat([frame for _, frame in chunks], ignore_index=True)
                    self.assertEqual(len(scoped), 2)
                    self.assertEqual(scoped["order_number"].tolist(), ["A", "B"])
                    self.assertEqual(scoped["department"].tolist(), ["华东", "华南"])
                    self.assertEqual(scoped["analysis_month"].tolist(), ["2026-06", "2026-07"])
                    self.assertEqual(scoped["amt"].tolist(), [1200.5, 300.0])
                    self.assertEqual(scoped["calc_final_profit"].tolist(), [120.25, -30.0])
                    self.assertNotIn("未映射字段", scoped.columns)
                    normalized[path.suffix] = scoped.reset_index(drop=True)
            pd.testing.assert_frame_equal(normalized[".csv"], normalized[".xlsx"])
            pd.testing.assert_frame_equal(normalized[".csv"], normalized[".parquet"])

    def test_bad_extension_and_missing_sheet_raise_clear_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); bad = base/"x.txt"; bad.write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(SourceReadError, "不支持的文件类型"):
                self._read(bad)
            excel = base/"x.xlsx"; self.frame.to_excel(excel, index=False, sheet_name="Data")
            with self.assertRaisesRegex(SourceReadError, "无法读取 Excel 工作表"):
                self._read(excel, "Missing")

    def test_custom_date_logical_column(self):
        """验证可以使用自定义 date_logical 列名代替硬编码 opration_date。"""
        frame2 = pd.DataFrame({
            "下单日期": ["2026-05-31", "2026-06-15", "2026-08-01"],
            "订": ["X", "Y", "Z"],
            "金": ["100", "200", "300"],
            "利": ["10", "20", "30"],
        })
        mapping2 = {
            "order_dt": "下单日期", "order_number": "订", "amt": "金", "calc_final_profit": "利",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.csv"
            frame2.to_csv(path, index=False, encoding="utf-8-sig")
            chunks = list(read_scoped_file(path, {"field_mapping": mapping2}, {"amt", "calc_final_profit"},
                                           self.start, self.end, 10, date_logical="order_dt"))
            scoped = pd.concat([c for _, c in chunks], ignore_index=True)
            self.assertEqual(len(scoped), 1)
            self.assertEqual(scoped["order_number"].iloc[0], "Y")
            self.assertEqual(scoped["analysis_month"].iloc[0], "2026-06")

    def test_apply_derived_dimensions_concat_and_copy(self):
        """验证 apply_derived_dimensions 的 concat（含空值<空>）和 copy 逻辑。"""
        chunk = pd.DataFrame({
            "group_first": ["华东", "", "华南"],
            "group_second": ["一部", "二部", ""],
            "sku": ["S1", "S2", "S3"],
        })
        derived = [
            {"name": "department", "type": "concat", "sources": ["group_first", "group_second"], "separator": " / "},
            {"name": "sku_copy", "type": "copy", "sources": ["sku"]},
        ]
        result = apply_derived_dimensions(chunk.copy(), derived)
        self.assertEqual(result["department"].tolist(), ["华东 / 一部", "<空> / 二部", "华南 / <空>"])
        self.assertEqual(result["sku_copy"].tolist(), ["S1", "S2", "S3"])

    def test_read_scoped_file_with_concat_derived_dimensions(self):
        """验证 read_scoped_file 通过 derived_dimensions 参数生成 concat 列。"""
        frame3 = pd.DataFrame({
            "日期": ["2026-06-10", "2026-07-10"],
            "订单": ["A1", "B1"],
            "金额": ["100", "200"],
            "利润": ["10", "20"],
            "一级": ["华东", "全托"],
            "二级": ["一部", "三部"],
        })
        mapping3 = {
            "opration_date": "日期", "order_number": "订单", "amt": "金额",
            "calc_final_profit": "利润", "group_first": "一级", "group_second": "二级",
        }
        derived = [
            {"name": "department", "type": "concat", "sources": ["group_first", "group_second"], "separator": " / "},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.csv"
            frame3.to_csv(path, index=False, encoding="utf-8-sig")
            chunks = list(read_scoped_file(path, {"field_mapping": mapping3}, {"amt", "calc_final_profit"},
                                           self.start, self.end, 10, date_logical="opration_date",
                                           derived_dimensions=derived))
            scoped = pd.concat([c for _, c in chunks], ignore_index=True)
            self.assertEqual(list(scoped["department"]), ["华东 / 一部", "全托 / 三部"])
            self.assertIn("department", scoped.columns)

    def test_missing_date_logical_raises_source_read_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.csv"
            self.frame.to_csv(path, index=False, encoding="utf-8-sig")
            with self.assertRaisesRegex(SourceReadError, "日期逻辑列"):
                list(read_scoped_file(path, {"field_mapping": self.mapping}, {"amt"},
                                      self.start, self.end, 1, date_logical="not_exist_date_col"))


if __name__ == "__main__": unittest.main()
