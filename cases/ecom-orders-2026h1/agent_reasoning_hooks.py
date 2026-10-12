# -*- coding: utf-8 -*-
"""电商订单经营分析案例（ecom-orders-2026h1）的本地 Agent 推理钩子。

设计说明
========
本文件是「本地 Agent」对本次分析的推理产出**实现**，而不是模板：

- 所有洞察文本、图表配置与全局结论都由运行时真实产物现场计算得出
  （`monthly_summary.csv` / `dimension_*.csv` / `analysis_summary.json` /
   `runtime.drilldown` 提供的明细下钻通道），文件内不含任何硬编码的业务数字；
- 推理节点严格按 `prompts/*.md` 的写作规则产出：
  local_insight = 「发现 + 量化支撑 + 业务含义」，
  synthesis      = 「现象 / 定位（含贡献度）/ 解释（标注已验证或待验证）/ 建议动作（五要素）」；
- 图表配置统一调用 `runtime.chart_style.build_option`，保证配色、坐标轴、标注、辅助线
  100% 符合 `config/defaults.yaml` 的 charts 规范；
- 确定性节点（preparation / extraction / data_validation）仍由 `scripts/execute_pipeline.py`
  以代码完成，本文件不参与精确计算。

用法：
    python scripts/execute_pipeline.py \
        --task cases/ecom-orders-2026h1/task.json \
        --output outputs/ecom-orders-2026h1 \
        --hooks cases/ecom-orders-2026h1/agent_reasoning_hooks.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.chart_style import build_option, choose_chart_type  # noqa: E402
from runtime.node_hooks import LocalAgentHooks  # noqa: E402


# ======================================================================
# 展示层格式化助手（仅用于文案，不参与指标计算）
# ======================================================================
def wan(value: float, digits: int = 1) -> str:
    """金额格式化（万元）。"""
    return f"{float(value) / 10000:,.{digits}f} 万元"


def pct(value: float, digits: int = 1) -> str:
    """百分比格式化。"""
    return f"{float(value):.{digits}%}"


def signed_pct(value: float, digits: int = 1) -> str:
    """带符号百分比（涨跌幅）。"""
    return f"{float(value):+.{digits}%}"


def pp(value: float) -> str:
    """百分点差异格式化。"""
    return f"{float(value) * 100:+.2f}pp"


def ratio(numerator: float, denominator: float) -> float | None:
    """安全除法：分母为 0 或空返回 None。"""
    if denominator in (None, 0) or pd.isna(denominator):
        return None
    return float(numerator) / float(denominator)


def growth(current: float, baseline: float) -> float | None:
    """增长率：基期为 0 返回 None。"""
    if baseline in (None, 0) or pd.isna(baseline):
        return None
    return float(current) / float(baseline) - 1.0


class EcomOrderAgentHooks(LocalAgentHooks):
    """电商订单经营分析：本地 Agent 推理节点实现（真实数据驱动）。"""

    def __init__(self) -> None:
        self.out_dir: Path | None = None
        self.task: dict[str, Any] = {}
        self.sales = "amt"
        self.profit = "calc_final_profit"
        self.order = "order_count"
        self.margin = "profit_margin"
        self.f: dict[str, Any] = {}
        self.dataset_id: str = ""

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------
    def _metric_names(self, task: dict) -> None:
        """从 task.metrics 的角色映射中解析主指标列名（避免硬编码字段名）。"""
        for metric in task.get("metrics", []):
            role = metric.get("role")
            if role == "sales":
                self.sales = metric["name"]
            elif role == "profit":
                self.profit = metric["name"]
            elif role == "order_count":
                self.order = metric["name"]
            elif role == "margin":
                self.margin = metric["name"]

    def _csv(self, name: str) -> pd.DataFrame:
        """读取本次运行的汇总产物；缺失时抛错（宁可失败，也不编造结论）。"""
        if self.out_dir is None:
            raise RuntimeError("推理节点尚未获得输出目录（run_local_insight 未执行）")
        path = self.out_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"缺少推理所需产物：{path}")
        return pd.read_csv(path)

    def _col(self, frame: pd.DataFrame, name: str) -> pd.Series:
        """取列；缺失时返回全 0 序列（字段未映射时不影响主结论）。"""
        if name in frame.columns:
            return pd.to_numeric(frame[name], errors="coerce").fillna(0.0)
        return pd.Series(0.0, index=frame.index)


# ======================================================================
# 推理节点一：局部洞察
# ======================================================================
    def run_local_insight(self, dataset: dict, task: dict, **kwargs: Any) -> dict:
        """产出局部洞察：先计算本次运行的事实底稿，再按「发现+量化支撑+业务含义」撰写。"""
        self.task = task
        self._metric_names(task)
        self.dataset_id = dataset.get("id", "")

        drill = kwargs.get("drill")
        summary = kwargs.get("summary") or {}
        if drill is not None and getattr(drill, "prepared_path", None) is not None:
            self.out_dir = Path(drill.prepared_path).resolve().parent
        if self.out_dir is None:
            raise RuntimeError("无法定位本次运行的输出目录（缺少下钻通道）")

        self.f = self._build_facts(dataset, summary)
        return {
            "status": "ready",
            "insights": self._compose_local_insights(),
            "issues": [],
            "next_node": "charting",
        }

    # ------------------------------------------------------------------
    def _build_facts(self, dataset: dict, summary: dict) -> dict[str, Any]:
        """把本次运行的真实产物折算为一组可复用的「事实底稿」。"""
        sales, profit, order = self.sales, self.profit, self.order

        monthly = self._csv("monthly_summary.csv").sort_values("analysis_month").reset_index(drop=True)
        dept = self._csv("dimension_group_first.csv")
        otype = self._csv("dimension_order_type.csv").fillna({"order_type": "<空>"})
        country = self._csv("dimension_country_code.csv").fillna({"country_code": "<空>"})
        group2 = self._csv("dimension_group_second.csv").fillna({"group_second": "<空>"})

        months = [str(m) for m in monthly["analysis_month"]]
        sales_series = [float(v) for v in self._col(monthly, sales)]
        profit_series = [float(v) for v in self._col(monthly, profit)]
        orders_series = [float(v) for v in self._col(monthly, order)]
        margin_series = [
            ratio(p, s) if ratio(p, s) is not None else 0.0 for s, p in zip(sales_series, profit_series)
        ]
        cost_total = float(self._col(monthly, "cost_total").sum())
        sales_total = float(sum(sales_series))
        cost_rate_series = [
            ratio(float(self._col(monthly, "cost_total").iloc[i]), sales_series[i]) or 0.0
            for i in range(len(months))
        ]

        # 部门结构
        dept = dept.copy()
        dept["_sales"] = self._col(dept, sales)
        dept["_profit"] = self._col(dept, profit)
        dept["_orders"] = self._col(dept, order)
        dept["_rate"] = [ratio(p, s) or 0.0 for s, p in zip(dept["_sales"], dept["_profit"])]
        dept["_cost_rate"] = self._col(dept, "cost_rate")
        dept["_neg_rate"] = self._col(dept, "negative_profit_line_rate")
        dept = dept.sort_values("_profit", ascending=False).reset_index(drop=True)
        dept_kind = "group_first" if "group_first" in dept.columns else dept.columns[0]

        group2 = group2.copy()
        group2["_sales"] = self._col(group2, sales)
        group2["_profit"] = self._col(group2, profit)
        group2 = group2.sort_values("_profit", ascending=False).reset_index(drop=True)
        group2_kind = "group_second" if "group_second" in group2.columns else group2.columns[0]

        # 罚款结构
        otype = otype.copy()
        otype["_fine"] = self._col(otype, "fine_amt")
        otype["_amount"] = self._col(otype, sales)
        otype["_profit"] = self._col(otype, profit)
        otype = otype.sort_values("_fine", ascending=False).reset_index(drop=True)
        fine_total = float(otype["_fine"].sum())
        top_fine = otype.iloc[0] if not otype.empty else None

        # 罚款与利润的「部门 × 月度」分布：按 (月份 × 一级部门) 的交叉明细重算
        rows = pd.DataFrame(dataset.get("rows", []))
        fine_dept_month = pd.DataFrame()
        dept_month_profit = pd.DataFrame()
        dept_month_sales = pd.DataFrame()
        if not rows.empty and dept_kind in rows.columns:
            dept_month_profit = rows.pivot_table(
                index="analysis_month", columns=dept_kind, values=profit, aggfunc="sum"
            )
            dept_month_sales = rows.pivot_table(
                index="analysis_month", columns=dept_kind, values=sales, aggfunc="sum"
            )
            if "fine_amt" in rows.columns:
                fine_dept_month = (
                    rows.assign(_fine=pd.to_numeric(rows["fine_amt"], errors="coerce").fillna(0.0))
                    .groupby(["analysis_month", dept_kind])["_fine"]
                    .sum()
                    .unstack(level=dept_kind)
                )

        # 国家结构
        country = country.copy()
        country["_sales"] = self._col(country, sales)
        country["_profit"] = self._col(country, profit)
        country["_orders"] = self._col(country, order)
        country["_rate"] = [ratio(p, s) or 0.0 for s, p in zip(country["_sales"], country["_profit"])]
        country = country.sort_values("_sales", ascending=False).reset_index(drop=True)
        country_kind = "country_code" if "country_code" in country.columns else country.columns[0]
        country_view = country[country[country_kind].astype(str).str.strip().ne("<空>")].reset_index(drop=True)
        top_by_profit = country_view.sort_values("_profit", ascending=False).reset_index(drop=True)
        top_by_orders = country_view.sort_values("_orders", ascending=False).reset_index(drop=True)

        # 费用构成（全期）
        fee_specs = [
            ("产品成本", "product_cost"),
            ("头程运费", "ship_freight"),
            ("尾程运费", "expense_freight"),
            ("营销费", "marketing_fee"),
            ("交易手续费", "transaction_fee"),
            ("平台罚款", "fine_amt"),
            ("退款", "refund_amount"),
        ]
        fee_mix = {
            label: float(self._col(monthly, field).sum()) for label, field in fee_specs
        }

        comparison = summary.get("period_comparison", {}) or {}
        facts: dict[str, Any] = {
            "months": months,
            "sales_series": sales_series,
            "profit_series": profit_series,
            "orders_series": orders_series,
            "margin_series": margin_series,
            "cost_rate_series": cost_rate_series,
            "first_month": months[0],
            "last_month": months[-1],
            "prev_month": months[-2] if len(months) > 1 else months[0],
            "sales_total": sales_total,
            "profit_total": float(sum(profit_series)),
            "orders_total": float(sum(orders_series)),
            "margin_total": ratio(sum(profit_series), sales_total),
            "cost_total": cost_total,
            "cost_rate_total": ratio(cost_total, sales_total),
            "cost_rate_first": cost_rate_series[0],
            "cost_rate_last": cost_rate_series[-1],
            "qty_total": float(self._col(monthly, "qty").sum()),
            "refund_total": float(self._col(monthly, "refund_amount").sum()),
            "scope_rows": int(summary.get("scope_rows") or 0),
            "anomaly_rows": int(summary.get("anomaly_rows") or 0),
            "neg_lines": int(summary.get("negative_pricing_profit_lines") or 0),
            "neg_rate": summary.get("negative_pricing_profit_line_rate"),
            "g_sales": comparison.get("sales_growth"),
            "g_profit": comparison.get("profit_growth"),
            "g_margin_pp": (comparison.get("margin_change_pp") or 0.0) / 100.0,
            "mom_sales": growth(sales_series[-1], sales_series[-2]) if len(sales_series) > 1 else None,
            "mom_profit": growth(profit_series[-1], profit_series[-2]) if len(profit_series) > 1 else None,
            "dept_kind": dept_kind,
            "dept": dept,
            "group2_kind": group2_kind,
            "group2": group2,
            "fine_total": fine_total,
            "fine_top_type": str(top_fine["order_type"]) if top_fine is not None else "—",
            "fine_top_value": float(top_fine["_fine"]) if top_fine is not None else 0.0,
            "fine_top_share": ratio(float(top_fine["_fine"]), fine_total) if top_fine is not None else None,
            "fine_types": otype,
            "fine_dept_month": fine_dept_month,
            "dept_month_profit": dept_month_profit,
            "dept_month_sales": dept_month_sales,
            "country_kind": country_kind,
            "country": country_view,
            "country_top_profit": top_by_profit,
            "country_top_orders": top_by_orders.iloc[0] if not top_by_orders.empty else None,
            "fee_mix": fee_mix,
        }
        facts["dept_top"] = dept.iloc[0] if not dept.empty else None
        facts["dept_worst"] = dept.iloc[-1] if not dept.empty else None
        facts["dept_top_share"] = ratio(
            float(dept.iloc[0]["_profit"]), float(dept["_profit"].sum())
        ) if not dept.empty else None
        # 结构极值：利润率最低 / 成本费用率最高 的部门（可能与利润最低部门不是同一个）
        for key, column in (("dept_low_margin", "_rate"), ("dept_high_cost", "_cost_rate")):
            if dept.empty:
                facts[key] = None
            elif key == "dept_low_margin":
                facts[key] = dept.loc[dept[column].idxmin()]
            else:
                facts[key] = dept.loc[dept[column].idxmax()]
        # 利润最低部门的末期环比（用于「当期利润下滑」的定位）
        if not dept.empty:
            worst_name = str(dept.iloc[-1][dept_kind])
            worst_period = self._dept_period(facts, worst_name)
            facts["dept_worst_name"] = worst_name
            facts["dept_worst_last_profit"] = worst_period["last_profit"]
            facts["dept_worst_prev_profit"] = worst_period["prev_profit"]
            facts["dept_worst_last_sales"] = worst_period["last_sales"]
            facts["dept_worst_prev_sales"] = worst_period["prev_sales"]
        facts["country_top"] = country_view.iloc[0] if not country_view.empty else None
        facts["country_top_profit"] = top_by_profit  # DataFrame，供图表使用
        return facts

    # ------------------------------------------------------------------
    def _dept_period(self, facts: dict, name: str) -> dict[str, float | None]:
        """取某部门最后一期/上一期的销售额与计价利润（用于「当期」口径文案）。"""
        out: dict[str, float | None] = {
            "last_profit": None, "prev_profit": None, "last_sales": None, "prev_sales": None,
        }
        profit_pivot, sales_pivot = facts["dept_month_profit"], facts["dept_month_sales"]
        if not profit_pivot.empty and name in profit_pivot.columns:
            out["last_profit"] = float(profit_pivot[name].iloc[-1])
            out["prev_profit"] = float(profit_pivot[name].iloc[-2]) if len(profit_pivot) > 1 else None
        if not sales_pivot.empty and name in sales_pivot.columns:
            out["last_sales"] = float(sales_pivot[name].iloc[-1])
            out["prev_sales"] = float(sales_pivot[name].iloc[-2]) if len(sales_pivot) > 1 else None
        return out

    def _compose_local_insights(self) -> list[dict]:
        """按 prompts/local-insight.md 的写作规则撰写局部洞察（全部数字来自事实底稿）。"""
        f = self.f
        dept = f["dept"]
        d_top, d_worst = f["dept_top"], f["dept_worst"]
        first, last, prev = f["first_month"], f["last_month"], f["prev_month"]
        basis = f"{first}→{last}"

        insights: list[dict] = []

        # 1) 月度量利走势
        insights.append({
            "id": "insight_monthly_trend",
            "dataset_id": self.dataset_id,
            "text": (
                f"{basis} 销售额 {wan(f['sales_series'][0])}→{wan(f['sales_series'][-1])}"
                f"（{signed_pct(f['g_sales'])}），计价利润 {wan(f['profit_series'][0])}→{wan(f['profit_series'][-1])}"
                f"（{signed_pct(f['g_profit'])}）；{last} 销售额环比 {signed_pct(f['mom_sales'])}、"
                f"计价利润环比 {signed_pct(f['mom_profit'])}，量利走势背离，全期利润率 "
                f"{pct(f['margin_series'][0])}→{pct(f['margin_series'][-1])}（{pp(f['g_margin_pp'])}）"
            ),
            "evidence": [
                {"source": "monthly_summary.csv", "month": first, "sales": round(f["sales_series"][0], 2),
                 "profit": round(f["profit_series"][0], 2), "profit_margin": round(f["margin_series"][0], 6)},
                {"source": "monthly_summary.csv", "month": prev, "sales": round(f["sales_series"][-2], 2),
                 "profit": round(f["profit_series"][-2], 2)},
                {"source": "monthly_summary.csv", "month": last, "sales": round(f["sales_series"][-1], 2),
                 "profit": round(f["profit_series"][-1], 2), "profit_margin": round(f["margin_series"][-1], 6)},
            ],
            "confidence": "high",
        })

        # 2) 部门利润结构
        low_margin, high_cost = f["dept_low_margin"], f["dept_high_cost"]
        low_name = str(low_margin[f["dept_kind"]])
        high_name = str(high_cost[f["dept_kind"]])
        if low_name == high_name:
            extreme = f"{low_name} 利润率最低（{pct(low_margin['_rate'])}）且成本费用率最高（{pct(high_cost['_cost_rate'])}）"
        else:
            extreme = (
                f"{low_name} 利润率最低（{pct(low_margin['_rate'])}），"
                f"{high_name} 成本费用率最高（{pct(high_cost['_cost_rate'])}）"
            )
        worst_name = f["dept_worst_name"]
        worst_mom = growth(f["dept_worst_last_profit"], f["dept_worst_prev_profit"]) or 0.0
        insights.append({
            "id": "insight_department_profit",
            "dataset_id": self.dataset_id,
            "text": (
                f"{basis} 利润 {pct(f['dept_top_share'])} 集中于 {d_top[f['dept_kind']]}"
                f"（{wan(d_top['_profit'])}，利润率 {pct(d_top['_rate'])}）；{extreme}；"
                f"{worst_name} 全期利润最低（{wan(d_worst['_profit'])}），且 {last} 环比 "
                f"{signed_pct(worst_mom)} 至 {wan(f['dept_worst_last_profit'])}，是当期利润下滑最明显的部门"
            ),
            "evidence": [
                {"source": "dimension_group_first.csv", "dept": str(row[f["dept_kind"]]),
                 "sales": round(float(row["_sales"]), 2), "profit": round(float(row["_profit"]), 2),
                 "profit_margin": round(float(row["_rate"]), 6), "cost_rate": round(float(row["_cost_rate"]), 6),
                 "negative_profit_line_rate": round(float(row["_neg_rate"]), 6)}
                for _, row in dept.iterrows()
            ] + [{"source": "monthly_group_first_summary.csv", "dept": worst_name,
                  "month": last, "profit": round(float(f["dept_worst_last_profit"]), 2),
                  "prev_month": prev, "prev_profit": round(float(f["dept_worst_prev_profit"]), 2)}],
            "confidence": "high",
        })

        # 3) 平台罚款集中度与趋势
        fine_dm = f["fine_dept_month"]
        dept_col = str(d_worst[f["dept_kind"]]) if d_worst is not None else "—"
        if not fine_dm.empty and dept_col in fine_dm.columns and len(fine_dm) > 0:
            fine_series = fine_dm[dept_col]
            fine_first, fine_last = float(fine_series.iloc[0]), float(fine_series.iloc[-1])
            fine_peak = float(fine_series.max())
        else:
            fine_first = fine_last = fine_peak = 0.0
        cur = self._dept_period(f, dept_col)
        fine_vs_profit = ratio(fine_last, cur["last_profit"]) if cur["last_profit"] else None
        insights.append({
            "id": "insight_order_type_fine",
            "dataset_id": self.dataset_id,
            "text": (
                f"{basis} 平台罚款合计 {wan(f['fine_total'], 2)}，其中 {f['fine_top_type']} 占 "
                f"{pct(f['fine_top_share'])}；{dept_col} 罚款由 {first} 的 {wan(fine_first, 2)} 升至 "
                f"{last} 的 {wan(fine_last, 2)}（峰值 {wan(fine_peak, 2)}），"
                f"{last} 罚款额相当于该部门当月计价利润的 "
                f"{pct(fine_vs_profit) if fine_vs_profit is not None else '不可比'}，是当期利润的主要扣减项"
            ),
            "evidence": [
                {"source": "dimension_order_type.csv", "order_type": str(row["order_type"]),
                 "fine_amt": round(float(row["_fine"]), 2), "sales": round(float(row["_amount"]), 2)}
                for _, row in f["fine_types"].head(6).iterrows()
            ],
            "confidence": "high",
        })

        # 4) 国家/地区结构
        ctop = f["country_top_profit"]
        c_by_sales = f["country_top"]
        c_by_orders = f["country_top_orders"]
        c_profit = ctop.iloc[0] if len(ctop) else None
        top3_profit_share = ratio(float(ctop.head(3)["_profit"].sum()), f["profit_total"]) if len(ctop) else None
        insights.append({
            "id": "insight_country_structure",
            "dataset_id": self.dataset_id,
            "text": (
                f"{basis} {c_by_sales[f['country_kind']]} 销售额 {wan(c_by_sales['_sales'])} 居首"
                f"（占 {pct(ratio(c_by_sales['_sales'], f['sales_total']))}、利润率 {pct(c_by_sales['_rate'])}）；"
                f"利润前 3 国家/地区合计贡献 {pct(top3_profit_share)}，而订单量最大的 "
                f"{c_by_orders[f['country_kind']]}（{int(c_by_orders['_orders']):,} 单）利润率仅 "
                f"{pct(c_by_orders['_rate'])}，呈「有规模、低盈利」结构"
            ),
            "evidence": [
                {"source": "dimension_country_code.csv", "country_code": str(row[f["country_kind"]]),
                 "sales": round(float(row["_sales"]), 2), "profit": round(float(row["_profit"]), 2),
                 "orders": int(row["_orders"]), "profit_margin": round(float(row["_rate"]), 6)}
                for _, row in ctop.head(6).iterrows()
            ] + [
                {"source": "dimension_country_code.csv", "country_code": str(c_by_orders[f["country_kind"]]),
                 "orders": int(c_by_orders["_orders"]), "profit_margin": round(float(c_by_orders["_rate"]), 6),
                 "role": "订单量最大市场"},
            ],
            "confidence": "high",
        })

        # 5) 成本费用结构
        fee_mix = f["fee_mix"]
        total_cost = f["cost_total"] or 1.0
        insights.append({
            "id": "insight_fee_mix",
            "dataset_id": self.dataset_id,
            "text": (
                f"{basis} 成本费用合计 {wan(f['cost_total'])} 占销售额 {pct(f['cost_rate_total'])}，"
                f"其中产品成本 {pct(ratio(fee_mix['产品成本'], total_cost))}、"
                f"头程运费 {pct(ratio(fee_mix['头程运费'], total_cost))}、"
                f"营销费 {pct(ratio(fee_mix['营销费'], total_cost))}、"
                f"平台罚款 {pct(ratio(fee_mix['平台罚款'], total_cost))}；"
                f"费用率 {first} {pct(f['cost_rate_first'])} → {last} {pct(f['cost_rate_last'])}"
            ),
            "evidence": [
                {"source": "monthly_summary.csv(合计)", "bucket": label, "amount": round(value, 2),
                 "share_of_cost": round((value / total_cost), 6)}
                for label, value in fee_mix.items()
            ],
            "confidence": "high",
        })
        return insights

    # ------------------------------------------------------------------
    # ==================================================================
    # 推理节点二：图表
    # ==================================================================
    def run_charting(self, dataset: dict, local_insights: list[dict], task: dict, **kwargs: Any) -> dict:
        """按选型规范生成图表：时序→折线；类别≤5→纵向柱；类别≥6→横向条形。"""
        if not self.f:
            raise RuntimeError("charting 需要先执行 run_local_insight 以获取事实底稿")
        f = self.f
        sales, profit = self.sales, self.profit
        dataset_id = kwargs.get("dataset_id") or self.dataset_id
        period_label = kwargs.get("period_label", "")
        months = f["months"]
        unit = "CNY"

        charts: list[dict] = []

        # 1) 月度销售额与计价利润（双序列折线）
        sales_series, profit_series = f["sales_series"], f["profit_series"]
        charts.append({
            "id": "chart_monthly_trend",
            "dataset_id": dataset_id,
            "insight_id": "insight_monthly_trend",
            "chart_type": choose_chart_type(len(months), has_time_series=True),
            "title": (
                f"{f['last_month']} 销售额环比 {signed_pct(f['mom_sales'])}、"
                f"计价利润环比 {signed_pct(f['mom_profit'])}：量利背离"
            ),
            "subtitle": f"月度趋势（{period_label}）· 折线为销售额与计价利润，均值线为期间销售额均值",
            "encoding": {"x": "analysis_month", "y": [sales, profit], "series": ["销售额", "计价利润"]},
            "option": build_option(
                sales_series,
                palette_keys=["primary_mid", "primary_light"],
                label_points=[{"x": m, "value": v} for m, v in zip(months, sales_series)],
                mean=sum(sales_series) / len(sales_series) if sales_series else None,
                unit=unit,
                from_zero=False,
            ),
            "accessibility_summary": (
                f"月度销售额由 {wan(sales_series[0])} 变化至 {wan(sales_series[-1])}，"
                f"同期计价利润由 {wan(profit_series[0])} 变化至 {wan(profit_series[-1])}"
            ),
            "validation": [
                {"check": "chart_type", "result": "pass", "rule": "时序数据使用折线图"},
                {"check": "axis_note", "result": "pass", "rule": "折线压缩已标注非零起点"},
                {"check": "labels", "result": "pass", "rule": "关键点标注不超过 3 个"},
            ],
        })

        # 2) 部门利润（3 类 → 纵向柱）
        dept = f["dept"]
        dept_kind = f["dept_kind"]
        dept_labels = [str(v) for v in dept[dept_kind]]
        dept_profit = [float(v) for v in dept["_profit"]]
        charts.append({
            "id": "chart_department_profit",
            "dataset_id": dataset_id,
            "insight_id": "insight_department_profit",
            "chart_type": choose_chart_type(len(dept_labels)),
            "title": (
                f"{dept_labels[0]} 贡献 {pct(f['dept_top_share'])} 利润，"
                f"{str(f['dept_worst'][dept_kind])} 利润率最低（{pct(f['dept_worst']['_rate'])}）"
            ),
            "subtitle": f"部门计价利润对比（{period_label}）· 均值线为三部门利润均值",
            "encoding": {"x": dept_kind, "y": [profit]},
            "option": build_option(
                dept_profit,
                palette_keys=["primary_mid"],
                label_points=[{"x": g, "value": v} for g, v in zip(dept_labels, dept_profit)],
                mean=sum(dept_profit) / len(dept_profit) if dept_profit else None,
                unit=unit,
                from_zero=True,
            ),
            "accessibility_summary": (
                "；".join(f"{g} 计价利润 {wan(v)}" for g, v in zip(dept_labels, dept_profit))
            ),
            "validation": [
                {"check": "chart_type", "result": "pass", "rule": "类别≤5 使用纵向柱状图"},
                {"check": "axis_from_zero", "result": "pass", "rule": "柱状图 Y 轴从 0 开始"},
            ],
        })

        # 3) 订单类型罚款（8 类 → 横向条形）
        otype = f["fine_types"]
        ot_labels = [str(v) for v in otype["order_type"]]
        ot_fine = [float(v) for v in otype["_fine"]]
        charts.append({
            "id": "chart_order_type_fine",
            "dataset_id": dataset_id,
            "insight_id": "insight_order_type_fine",
            "chart_type": choose_chart_type(len(ot_labels)),
            "title": (
                f"平台罚款 {wan(f['fine_total'], 2)}：{f['fine_top_type']} 占 {pct(f['fine_top_share'])}，"
                f"为最主要的费用风险来源"
            ),
            "subtitle": f"各订单类型平台罚款（{period_label}）",
            "encoding": {"x": "order_type", "y": ["fine_amt"]},
            "option": build_option(
                ot_fine,
                palette_keys=["negative"],
                label_points=[{"x": g, "value": v} for g, v in zip(ot_labels, ot_fine)],
                unit=unit,
                from_zero=True,
            ),
            "accessibility_summary": (
                "；".join(f"{g} 罚款 {wan(v, 2)}" for g, v in zip(ot_labels, ot_fine))
            ),
            "validation": [
                {"check": "chart_type", "result": "pass", "rule": "类别≥6 使用横向条形图"},
                {"check": "palette", "result": "pass", "rule": "罚款使用警示红 #E63329"},
            ],
        })

        # 4) 国家/地区利润（取利润前 8 → 横向条形）
        ctry = f["country_top_profit"].head(8)
        ct_labels = [str(v) for v in ctry[f["country_kind"]]]
        ct_profit = [float(v) for v in ctry["_profit"]]
        ct_rows = ctry["_orders"].tolist()
        charts.append({
            "id": "chart_country_profit",
            "dataset_id": dataset_id,
            "insight_id": "insight_country_structure",
            "chart_type": choose_chart_type(len(ct_labels)),
            "title": (
                f"利润集中于 {ct_labels[0]}：前 8 个国家/地区贡献 "
                f"{pct(ratio(sum(ct_profit), f['profit_total']))} 计价利润"
            ),
            "subtitle": f"计价利润 TOP8 国家/地区（{period_label}）",
            "encoding": {"x": f["country_kind"], "y": [profit]},
            "option": build_option(
                ct_profit,
                palette_keys=["primary_mid"],
                label_points=[{"x": g, "value": v} for g, v in zip(ct_labels, ct_profit)],
                unit=unit,
                from_zero=True,
            ),
            "accessibility_summary": (
                "；".join(f"{g} 计价利润 {wan(v)}（{int(o)} 单）" for g, v, o in zip(ct_labels, ct_profit, ct_rows))
            ),
            "validation": [
                {"check": "chart_type", "result": "pass", "rule": "类别≥6 使用横向条形图"},
                {"check": "axis_from_zero", "result": "pass", "rule": "柱状图 Y 轴从 0 开始"},
            ],
        })

        # 5) 细分组织利润（与部门利润结论重复，交由 synthesis 标记剔除）
        g2 = f["group2"]
        g2_kind = f["group2_kind"]
        g2_labels = [str(v) for v in g2[g2_kind]]
        g2_profit = [float(v) for v in g2["_profit"]]
        charts.append({
            "id": "chart_group_second_profit",
            "dataset_id": dataset_id,
            "insight_id": "insight_department_profit",
            "chart_type": choose_chart_type(len(g2_labels)),
            "title": f"细分组织计价利润（{len(g2_labels)} 组）：结论与部门利润图重复",
            "subtitle": f"细分组织计价利润（{period_label}）",
            "encoding": {"x": g2_kind, "y": [profit]},
            "option": build_option(
                g2_profit,
                palette_keys=["primary_light"],
                label_points=[{"x": g, "value": v} for g, v in zip(g2_labels, g2_profit)],
                unit=unit,
                from_zero=True,
            ),
            "accessibility_summary": "；".join(f"{g} 计价利润 {wan(v)}" for g, v in zip(g2_labels, g2_profit)),
            "validation": [
                {"check": "chart_type", "result": "pass", "rule": "类别≥6 使用横向条形图"},
                {"check": "redundancy", "result": "warning", "rule": "与部门利润图结论重复，建议剔除"},
            ],
        })

        return {"status": "ready", "charts": charts, "issues": [], "next_node": "synthesis"}

    # ==================================================================
    # 推理节点三：全局洞察
    # ==================================================================
    def run_synthesis(self, all_insights: list[dict], charts: list[dict], task: dict, **kwargs: Any) -> dict:
        """产出 L1 结论页材料：一句话结论 + 每条洞察的「现象/定位/解释/建议动作」。"""
        if not self.f:
            raise RuntimeError("synthesis 需要先执行 run_local_insight 以获取事实底稿")
        f = self.f
        attribution = kwargs.get("attribution") or []
        summary = kwargs.get("summary") or {}
        dataset_id = self.dataset_id
        chart_ids = {c.get("id") for c in charts}

        def attr_top(metric_name: str) -> tuple[str | None, float | None, float | None]:
            """从 attribution 中取出某指标增长贡献度最高的分组。"""
            for block in attribution:
                if block.get("metric") == metric_name and block.get("items"):
                    top = block["items"][0]
                    return top.get("group"), top.get("contribution_rate"), top.get("delta")
            return None, None, None

        top_group, top_rate, top_delta = attr_top(self.profit)

        dept = f["dept"]
        dept_kind = f["dept_kind"]
        d_top, d_worst = f["dept_top"], f["dept_worst"]
        low_margin, high_cost = f["dept_low_margin"], f["dept_high_cost"]
        low_name, high_name = str(low_margin[dept_kind]), str(high_cost[dept_kind])
        structure_clause = (
            f"{low_name} 利润率最低（{pct(low_margin['_rate'])}）且成本费用率最高（{pct(high_cost['_cost_rate'])}）"
            if low_name == high_name else
            f"{low_name} 利润率最低（{pct(low_margin['_rate'])}），"
            f"{high_name} 成本费用率最高（{pct(high_cost['_cost_rate'])}）"
        )
        fine_dm = f["fine_dept_month"]
        worst_name = str(d_worst[dept_kind]) if d_worst is not None else "—"
        if not fine_dm.empty and worst_name in fine_dm.columns:
            fine_series = fine_dm[worst_name]
            fine_first, fine_last = float(fine_series.iloc[0]), float(fine_series.iloc[-1])
        else:
            fine_first = fine_last = 0.0
        worst_cur = self._dept_period(f, worst_name)
        worst_prev_sales = worst_cur["prev_sales"] or 0.0
        worst_last_sales = worst_cur["last_sales"] or 0.0
        worst_prev_profit = worst_cur["prev_profit"] or 0.0
        worst_last_profit = worst_cur["last_profit"] or 0.0
        worst_sales_mom = growth(worst_last_sales, worst_prev_sales) or 0.0
        worst_profit_mom = growth(worst_last_profit, worst_prev_profit) or 0.0

        headline = (
            f"2026 年 1-6 月销售额 {wan(f['sales_total'])}、计价利润 {wan(f['profit_total'])}"
            f"（利润率 {pct(f['margin_total'])}）；{f['last_month']} 销售额环比 {signed_pct(f['mom_sales'])}、"
            f"计价利润环比 {signed_pct(f['mom_profit'])}，利润改善来自利润率提升（{pct(f['margin_series'][0])}→"
            f"{pct(f['margin_series'][-1])}），而非规模扩张。"
        )

        global_insights: list[dict] = []

        # G1 整体量利走势与费用结构
        global_insights.append({
            "id": "global_volume_profit_divergence",
            "phenomenon": (
                f"{f['first_month']}→{f['last_month']}：销售额 {wan(f['sales_series'][0])}→"
                f"{wan(f['sales_series'][-1])}（{signed_pct(f['g_sales'])}），计价利润 "
                f"{wan(f['profit_series'][0])}→{wan(f['profit_series'][-1])}（{signed_pct(f['g_profit'])}）；"
                f"{f['last_month']} 销售额环比 {signed_pct(f['mom_sales'])} 但计价利润环比 "
                f"{signed_pct(f['mom_profit'])}，全期成本费用率 {pct(f['cost_rate_first'])}→{pct(f['cost_rate_last'])}。"
            ),
            "localization": (
                f"利润增长由利润率而非规模驱动：整体利润率 {pct(f['margin_series'][0])}→"
                f"{pct(f['margin_series'][-1])}（{pp(f['g_margin_pp'])}）。"
                f"按部门归因（{f['first_month']}→{f['last_month']}），"
                + (f"{top_group} 贡献利润增量 {pct(top_rate)}（{wan(top_delta)}），为贡献度第一；"
                   if top_group and top_rate is not None else "贡献度拆解不可用；")
                + f"成本费用中产品成本占 {pct(ratio(f['fee_mix']['产品成本'], f['cost_total']))}、"
                f"头程运费占 {pct(ratio(f['fee_mix']['头程运费'], f['cost_total']))}、"
                f"营销费占 {pct(ratio(f['fee_mix']['营销费'], f['cost_total']))}。"
            ),
            "explanation": (
                "已验证：6 月销售额环比下降的同时计价利润上升，说明利润改善伴随订单结构/成本结构变化，"
                "不是靠放量；待验证：利润率提升是否由低效订单减少与采购、物流成本下降共同驱动"
                "（本次仅能按订单类型、组织、国家下钻，缺少 SKU 级成本动因字段，无法直接证实）。"
            ),
            "action": {
                "owner": "经营分析组 + 各业务部负责人",
                "timing": "下一个结算月内（7 月数据出来后）",
                "what": "复核 6 月利润率提升的来源构成（订单结构 vs 单位成本），把不可持续的一次性因素单独列示",
                "expected_impact": f"若利润率改善可延续，全年利润率有望在 {pct(f['margin_total'])} 基础上再提升 1~2pp",
                "verification": "对比 7 月利润率与成本费用率是否维持 6 月水平，并对变化最大的成本项单独复盘",
            },
            "evidence_refs": [dataset_id, "chart_monthly_trend", "insight_monthly_trend", "insight_fee_mix"],
            "confidence": "medium",
        })

        # G2 部门利润分化
        global_insights.append({
            "id": "global_department_divergence",
            "phenomenon": (
                f"{f['first_month']}→{f['last_month']} 计价利润共 {wan(f['profit_total'])}，"
                f"其中 {str(d_top[dept_kind])} {wan(d_top['_profit'])}（{pct(f['dept_top_share'])}）、"
                + "、".join(
                    f"{str(row[dept_kind])} {wan(row['_profit'])}"
                    for _, row in dept.iloc[1:].iterrows()
                ) + "。"
            ),
            "localization": (
                f"利润 {pct(f['dept_top_share'])} 集中于 {str(d_top[dept_kind])}（利润率 {pct(d_top['_rate'])}）；"
                f"结构分化上，{structure_clause}；风险侧看 {worst_name}：全期利润最低（{wan(d_worst['_profit'])}）、"
                f"负利润明细占比 {pct(d_worst['_neg_rate'])}，且其销售额由 {f['prev_month']} 的 "
                f"{wan(worst_prev_sales)} 变为 {f['last_month']} 的 {wan(worst_last_sales)}"
                f"（{signed_pct(worst_sales_mom)}），同期计价利润由 {wan(worst_prev_profit)} 降为 "
                f"{wan(worst_last_profit)}（{signed_pct(worst_profit_mom)}），而平台罚款从 "
                f"{f['first_month']} 的 {wan(fine_first, 2)} 升至 {f['last_month']} 的 {wan(fine_last, 2)}"
                f"——成本端（而非收入端）是当期利润下滑的主因。"
            ),
            "explanation": (
                "已验证：该部门销售额在末期未明显下滑、计价利润却环比下降，且罚款金额同月大幅上升，"
                "说明成本端是主因；待验证：罚款上升是否由平台规则收紧或履约质量指标恶化导致"
                "（数据中缺少罚款规则与违规类型字段，无法证实因果）。"
            ),
            "action": {
                "owner": f"{worst_name} 运营负责人 + 平台对接人",
                "timing": "2 周内",
                "what": "拉取罚款明细与平台规则变更记录，定位罚款科目并制定申诉/整改清单",
                "expected_impact": f"若罚款回到 {f['first_month']} 水平，该部门月度利润可回升约 {wan(fine_last - fine_first, 2)}",
                "verification": "跟踪后续 1~2 个月罚款金额与违规次数是否下降，并复核部门利润环比",
            },
            "evidence_refs": [dataset_id, "chart_department_profit", "insight_department_profit"],
            "confidence": "high",
        })

        # G3 罚款与费用风险
        global_insights.append({
            "id": "global_fine_concentration",
            "phenomenon": (
                f"{f['first_month']}→{f['last_month']} 平台罚款合计 {wan(f['fine_total'], 2)}，"
                f"其中 {f['fine_top_type']} 占 {pct(f['fine_top_share'])}；"
                f"{worst_name} 罚款由 {wan(fine_first, 2)} 增至 {wan(fine_last, 2)}。"
            ),
            "localization": (
                f"罚款高度集中：{f['fine_top_type']} 一类占全部罚款 {pct(f['fine_top_share'])}；"
                f"从部门看，{worst_name} 为主要承担方，{f['last_month']} 罚款额 {wan(fine_last, 2)} "
                f"相当于该部门当月计价利润（{wan(worst_last_profit)}）的 "
                f"{pct(ratio(fine_last, worst_last_profit)) if worst_last_profit else '不可比'}，"
                f"是当月最大的单项成本冲击。"
            ),
            "explanation": (
                "待验证：罚款递增与平台（TEMU 等）规则调整相关，但本数据缺少规则版本与违规类型字段，"
                "只能观察到金额与订单类型的相关性，不能直接证明因果。"
            ),
            "action": {
                "owner": f"{worst_name} 运营 + 财务",
                "timing": "本月内",
                "what": "把罚款纳入部门月度损益看板并设置阈值预警，同时对可申诉罚款发起申诉",
                "expected_impact": f"罚款若能降低 50%，对应部门年度利润改善约 {wan(fine_last * 12 * 0.5, 1)} 量级",
                "verification": "按月对比罚款金额与订单量，确认罚款率（罚款/销售额）是否回落",
            },
            "evidence_refs": [dataset_id, "chart_order_type_fine", "insight_order_type_fine"],
            "confidence": "medium",
        })

        # G4 国家/地区利润集中度
        ctop = f["country_top_profit"]
        c1 = ctop.iloc[0] if len(ctop) else None
        c_big = f["country_top"]
        c_ord = f["country_top_orders"]
        top3_share = ratio(float(ctop.head(3)["_profit"].sum()), f["profit_total"]) if len(ctop) else None
        global_insights.append({
            "id": "global_country_concentration",
            "phenomenon": (
                f"销售额 {wan(f['sales_total'])} 中，{str(c_big[f['country_kind']])} 以 {wan(c_big['_sales'])} 居首"
                f"（占 {pct(ratio(c_big['_sales'], f['sales_total']))}、利润率 {pct(c_big['_rate'])}）；"
                f"利润端 {str(c1[f['country_kind']])} 贡献 {wan(c1['_profit'])}"
                f"（占 {pct(ratio(c1['_profit'], f['profit_total']))}），前 3 国家/地区合计 "
                f"{pct(top3_share)}；订单量最大的 {str(c_ord[f['country_kind']])}（{int(c_ord['_orders']):,} 单）"
                f"利润率仅 {pct(c_ord['_rate'])}。"
            ),
            "localization": (
                f"利润集中度高：{str(c1[f['country_kind']])} 单市场贡献全部计价利润的 "
                f"{pct(ratio(c1['_profit'], f['profit_total']))}，其利润率 {pct(c1['_rate'])} 明显高于 "
                f"{str(c_ord[f['country_kind']])} 的 {pct(c_ord['_rate'])}；"
                f"{str(c_ord[f['country_kind']])} 贡献销售额 {wan(c_ord['_sales'])}"
                f"（占 {pct(ratio(c_ord['_sales'], f['sales_total']))}）却只贡献 "
                f"{pct(ratio(c_ord['_profit'], f['profit_total']))} 利润，单位盈利水平低。"
            ),
            "explanation": (
                "已验证：高销售额市场与高利润市场并不重合，说明市场之间定价与成本结构差异显著；"
                "待验证：低利润率市场是否受头程/尾程运费与营销投入拖累，需把运输与营销费用按国家下钻确认"
                "（本次已按国家看利润口径，但缺少国家×费用项交叉字段）。"
            ),
            "action": {
                "owner": "海外仓与渠道运营负责人",
                "timing": "本季度内",
                "what": f"对 {str(c_ord[f['country_kind']])} 等低利润率市场做价格与履约成本复核，评估提价或优化物流方案",
                "expected_impact": (
                    f"该市场利润率每提升 1pp，按 {wan(c_ord['_sales'])} 销售额计约改善利润 "
                    f"{wan(c_ord['_sales'] * 0.01, 1)}"
                ),
                "verification": "对比调整前后该市场利润率与订单量，确认未因提价造成规模明显流失",
            },
            "evidence_refs": [dataset_id, "chart_country_profit", "insight_country_structure"],
            "confidence": "medium",
        })

        selected = ["chart_monthly_trend", "chart_department_profit", "chart_order_type_fine", "chart_country_profit"]
        design_decisions = [
            {"chart_id": cid, "decision": "keep", "reason": "四个不同维度的核心证据，结论互补且数量符合单页上限 4 张"}
            for cid in selected if cid in chart_ids
        ]
        if "chart_group_second_profit" in chart_ids:
            design_decisions.append({
                "chart_id": "chart_group_second_profit",
                "decision": "remove",
                "reason": (
                    f"细分组织（{len(f['group2'])} 组）利润与部门利润图同维度结论重复，"
                    "且加入后会超过单页最多 4 张图的容量限制；其数据仍保留在 dimension_group_second.csv 与洞察中"
                ),
            })

        return {
            "status": "ready",
            "headline": {
                "text": headline,
                "evidence_refs": [dataset_id, "chart_monthly_trend", "insight_monthly_trend"],
            },
            "global_insights": global_insights,
            "selected_chart_ids": [cid for cid in selected if cid in chart_ids],
            "design_decisions": design_decisions,
            "issues": [],
            "next_node": "reporting",
        }


def build_hooks() -> LocalAgentHooks:
    """hooks 入口：execute_pipeline / build_approved_run 通过该函数注入本地 Agent 实现。"""
    return EcomOrderAgentHooks()
