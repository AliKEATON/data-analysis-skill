"""测试用本地 Agent 推理夹具：模拟本地 Agent 覆写推理节点，返回合约内的合法产物。

用途：单元测试中代替真实本地 Agent 的推理实现，验证「Agent 提供推理结果时链路可跑通」；
与「基类 LocalAgentHooks 未覆写时抛 AgentReasoningRequired」形成对照。
"""
from __future__ import annotations

from runtime.node_hooks import LocalAgentHooks


class SampleAgentHooks(LocalAgentHooks):
    """示例本地 Agent 推理实现：intake/planning 沿用基类，洞察/合成由本类产出。"""

    def run_local_insight(self, dataset: dict, task: dict, **kwargs) -> dict:
        """返回两条模拟本地 Agent 洞察，引用真实 dataset_id。"""
        dataset_id = dataset.get("id", "")
        return {
            "status": "ready",
            "insights": [
                {
                    "id": "insight_monthly_trend",
                    "dataset_id": dataset_id,
                    "text": "模拟本地 Agent 洞察：月度销售额与利润变化趋势。",
                    "evidence": [{"source": "monthly_summary.csv", "metric": "amt"}],
                    "confidence": "high",
                },
                {
                    "id": "insight_department_risk",
                    "dataset_id": dataset_id,
                    "text": "模拟本地 Agent 洞察：部门层面的负利润风险。",
                    "evidence": [{"source": "department_summary.csv", "metric": "calc_final_profit"}],
                    "confidence": "high",
                },
            ],
            "issues": [],
            "next_node": "charting",
        }

    def run_charting(self, dataset: dict, local_insights: list[dict], task: dict, **kwargs) -> dict:
        """返回两张模拟图表：选型、配色、坐标轴、标注、辅助线均按规范声明。"""
        from runtime.chart_style import build_option, choose_chart_type

        dataset_id = kwargs.get("dataset_id") or dataset.get("id", "")
        sales_label = kwargs.get("sales_label", "销售额")
        profit_label = kwargs.get("profit_label", "利润")
        primary_entity_label = kwargs.get("primary_entity_label", "部门")
        insight_ids = [i.get("id", "") for i in local_insights if i.get("id")]
        monthly_id = "insight_monthly_trend" if "insight_monthly_trend" in insight_ids else (insight_ids[0] if insight_ids else "")
        dept_id = "insight_department_risk" if "insight_department_risk" in insight_ids else (insight_ids[-1] if len(insight_ids) > 1 else "")
        points = [{"x": "t1", "value": 100.0}, {"x": "t2", "value": 120.0}]
        option = build_option([100.0, 120.0], label_points=points, mean=110.0, unit="CNY")
        return {
            "status": "ready",
            "charts": [
                {"id": "chart_monthly_sales_profit", "dataset_id": dataset_id, "insight_id": monthly_id,
                 "chart_type": choose_chart_type(2, has_time_series=True), "title": f"月度{sales_label}与{profit_label}",
                 "subtitle": "模拟", "encoding": {}, "option": option,
                 "accessibility_summary": "月度对比", "validation": []},
                {"id": "chart_department_profit", "dataset_id": dataset_id, "insight_id": dept_id,
                 "chart_type": choose_chart_type(2), "title": f"{primary_entity_label}{profit_label}",
                 "subtitle": "模拟", "encoding": {}, "option": option,
                 "accessibility_summary": "部门对比", "validation": []},
            ],
            "issues": [],
            "next_node": "synthesis",
        }

    def run_synthesis(self, all_insights: list[dict], charts: list[dict], task: dict, **kwargs) -> dict:
        """返回一条模拟全局洞察（含「现象/定位/解释/建议动作」四小段），证据引用真实对象。"""
        chart_ids = [c.get("id", "") for c in charts if c.get("id")]
        dataset_ids = [i.get("dataset_id", "") for i in all_insights if i.get("dataset_id")]
        insight_ids = [i.get("id", "") for i in all_insights if i.get("id")]
        refs = [r for r in [*(dataset_ids[:1]), *(insight_ids[:1])] if r]
        if len(refs) < 2:
            refs = (refs + chart_ids)[:2]
        headline_text = "模拟结论：核心指标在报告期较基期发生变化，主要由首个分组驱动。"
        return {
            "status": "ready",
            "headline": {"text": headline_text, "evidence_refs": refs[:1] or ["ds1"]},
            "global_insights": [
                {
                    "id": "global_agent",
                    "phenomenon": "模拟现象：核心指标从基期到报告期发生变化。",
                    "localization": "模拟定位：按贡献度排序，首个数据集的分组贡献最大。",
                    "explanation": "模拟解释：疑似结构变化导致（待验证）。",
                    "action": {
                        "owner": "模拟责任方",
                        "timing": "模拟时间窗",
                        "what": "模拟建议动作",
                        "expected_impact": "模拟预期影响量级",
                        "verification": "模拟验证方式",
                    },
                    "evidence_refs": refs,
                    "confidence": "medium",
                }
            ],
            "selected_chart_ids": chart_ids,
            "design_decisions": [
                {"chart_id": chart_id, "decision": "keep", "reason": "模拟本地 Agent 判断：支撑结论"} for chart_id in chart_ids
            ],
            "issues": [],
            "next_node": "reporting",
        }
