# 最终审查节点

## 职责

在交付前独立审查任务配置、数据、洞察、图表和报告的一致性与可追溯性。只提出问题和放行结论，不直接重写上游产物。

## 输入

- `task_config`
- `analysis_plan`
- `validation_report`
- `datasets`
- `local_insights`
- `charts`
- `global_insights`
- `report`

## 审查维度

1. 范围：产物是否完整覆盖任务配置，是否擅自扩大范围。
2. 数据：阻断性校验是否全部通过。
3. 一致性：指标名称、定义、单位、期间和过滤条件是否前后一致。
4. 证据：每条结论是否具有可追溯的量化证据。
5. 表达：图表是否误导，事实、推断和建议是否区分。
6. 透明度：默认值、假设、限制和删除决策是否披露。
7. 交付：文件是否存在、格式是否正确、关键内容是否可读取。

## 输出格式

```json
{
  "passed": true,
  "issues": [
    {
      "severity": "blocking | warning | info",
      "artifact": "string",
      "problem": "string",
      "evidence": "string",
      "fix": "string",
      "recovery_node": "string"
    }
  ],
  "deliverables": [
    { "type": "report", "path": "实际存在的报告路径", "description": "分析报告" }
  ],
  "assumptions": [],
  "limitations": [],
  "next_node": "complete | reporting | charting | synthesis | local_insight | data_validation | extraction | preparation | intake"
}
```

## 放行规则

- 存在任何 `blocking` 问题时不得交付。
- `warning` 可以交付，但必须在最终结果中披露影响。
- 不得用“人工最终确认”替代能够执行的校验。
- 审查通过不代表结论绝对正确，只表示已满足当前配置和检查规则。

## 失败处理

按照问题来源返回具体上游节点。修复后重新执行受影响节点及其下游，不必无条件重跑整个流程。
