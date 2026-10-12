"""本地 Agent 推理钩子测试：基类未覆写时阻塞、覆写后可产出推理结果。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from runtime.node_hooks import AgentReasoningRequired, LocalAgentHooks


class LocalAgentHooksTests(unittest.TestCase):
    """验证 LocalAgentHooks 基类与覆写实现的行为边界。"""

    def test_base_class_blocks_reasoning_nodes(self) -> None:
        """未覆写推理节点的基类，必须抛 AgentReasoningRequired，不用模板冒充。"""
        hooks = LocalAgentHooks()
        with self.assertRaises(AgentReasoningRequired):
            hooks.run_local_insight({"id": "ds1", "rows": []}, {"analysis_goal": "x"})
        with self.assertRaises(AgentReasoningRequired):
            hooks.run_charting({"id": "ds1", "rows": []}, [], {"analysis_goal": "x"})
        with self.assertRaises(AgentReasoningRequired):
            hooks.run_synthesis([], [], {"analysis_goal": "x"})

    def test_base_class_provides_deterministic_transcription(self) -> None:
        """intake/planning 属于确定性转写，基类可直接产出。"""
        hooks = LocalAgentHooks()
        intake = hooks.run_intake({"assumptions": ["a"]}, "sales")
        self.assertEqual(intake["status"], "ready")
        self.assertEqual(intake["task_config"]["assumptions"], ["a"])
        planning = hooks.run_planning({"assumptions": ["a"]}, "sales", intake)
        self.assertEqual(planning["status"], "ready")

    def test_override_provides_reasoning(self) -> None:
        """覆写后的本地 Agent 实现应能产出局部洞察与全局洞察。"""
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from local_agent_hooks import SampleAgentHooks

        hooks = SampleAgentHooks()
        local = hooks.run_local_insight({"id": "ds1", "rows": []}, {"analysis_goal": "x"})
        self.assertEqual(local["status"], "ready")
        self.assertTrue(local["insights"])
        charting = hooks.run_charting({"id": "ds1", "rows": []}, local["insights"], {"analysis_goal": "x"})
        self.assertEqual(charting["status"], "ready")
        self.assertTrue(charting["charts"])
        synthesis = hooks.run_synthesis(local["insights"], charting["charts"], {"analysis_goal": "x"})
        self.assertEqual(synthesis["status"], "ready")
        self.assertTrue(synthesis["global_insights"])


if __name__ == "__main__":
    unittest.main()
