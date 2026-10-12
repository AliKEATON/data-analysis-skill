"""本地 Agent 推理钩子接口：定义推理节点的注入协议，不涉及任何外部大模型。

设计原则：
- Skill 固定由「本地 Agent」（当前对话模型或子 Agent）承担推理节点的产出，
  不接入外部大模型，因此这里没有「是否配置模型」的分支判断。
- 确定性节点（intake / planning 对已确认任务的转写）由基类直接提供；
- 推理节点（local_insight / charting / synthesis）必须由本地 Agent 覆写实现，
  未覆写时抛 AgentReasoningRequired 提示补全，绝不用模板冒充洞察。
"""
from __future__ import annotations
from typing import Protocol


class AgentReasoningRequired(RuntimeError):
    """推理节点缺少本地 Agent 产出时抛出，用于提示调用方补全推理结果。"""


class NodeHooks(Protocol):
    """节点钩子协议：推理内容由本地 Agent 产出。"""

    def run_intake(self, task: dict, domain: str | None) -> dict:
        """intake 节点钩子：返回符合 intake-result.schema.json 的结果字典。"""
        ...

    def run_planning(self, task: dict, domain: str | None, intake_result: dict) -> dict:
        """planning 节点钩子：返回符合 analysis-plan.schema.json 的结果字典。"""
        ...

    def run_local_insight(self, dataset: dict, task: dict, **kwargs) -> dict:
        """local_insight 节点钩子：由本地 Agent 产出洞察。"""
        ...

    def run_charting(self, dataset: dict, local_insights: list[dict], task: dict, **kwargs) -> dict:
        """charting 节点钩子：由本地 Agent 依据局部洞察产出图表配置。"""
        ...

    def run_synthesis(self, all_insights: list[dict], charts: list[dict], task: dict, **kwargs) -> dict:
        """synthesis 节点钩子：由本地 Agent 产出全局洞察。"""
        ...


class LocalAgentHooks:
    """本地 Agent 推理钩子基类：确定性转写可直接使用，推理节点需由 Agent 覆写。

    使用方式：创建一个子类，覆写 run_local_insight、run_charting 与 run_synthesis，
    返回本地 Agent 依据真实数据产出的洞察与图表；再把该实例注入
    execute_pipeline.run(..., hooks=...) 或 build_approved_run.build_run(..., hooks=...)。
    参考实现见 my_case/agent_reasoning_hooks.py。
    """

    def run_intake(self, task: dict, domain: str | None) -> dict:
        """intake 对已确认任务属于确定性转写：任务已明确，无需澄清。"""
        return {
            "status": "ready",
            "questions": [],
            "task_config": task,
            "missing_fields": [],
            "ambiguous_fields": [],
            "assumptions": task.get("assumptions", []),
        }

    def run_planning(self, task: dict, domain: str | None, intake_result: dict) -> dict:
        """planning 对已确认任务属于确定性转写：计划结构由 task 决定。"""
        return {"status": "ready", "assumptions": task.get("assumptions", [])}

    def run_local_insight(self, dataset: dict, task: dict, **kwargs) -> dict:
        """local_insight 必须由本地 Agent 覆写提供，未覆写时提示补全。"""
        raise AgentReasoningRequired(
            "local_insight 需要本地 Agent 产出：请继承 LocalAgentHooks 并覆写 run_local_insight，"
            "依据 prompts/local-insight.md 与真实数据产出洞察后注入。"
        )

    def run_charting(self, dataset: dict, local_insights: list[dict], task: dict, **kwargs) -> dict:
        """charting 必须由本地 Agent 覆写提供，未覆写时提示补全。"""
        raise AgentReasoningRequired(
            "charting 需要本地 Agent 产出：请继承 LocalAgentHooks 并覆写 run_charting，"
            "依据 prompts/chart.md 与局部洞察产出图表配置后注入。"
        )

    def run_synthesis(self, all_insights: list[dict], charts: list[dict], task: dict, **kwargs) -> dict:
        """synthesis 必须由本地 Agent 覆写提供，未覆写时提示补全。"""
        raise AgentReasoningRequired(
            "synthesis 需要本地 Agent 产出：请继承 LocalAgentHooks 并覆写 run_synthesis，"
            "依据 prompts/global-insight.md 与局部洞察产出全局结论后注入。"
        )
