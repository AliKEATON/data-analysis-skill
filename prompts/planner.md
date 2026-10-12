# 分析计划节点

## 职责

把已验证的任务配置转化为可执行、可校验的分析计划。只制定计划，不读取数据或生成结论。

## 输入

- `task_config`：通过 Schema 校验的任务配置。
- `domain_profile`：已选择的领域配置。
- `available_tools`：当前可用的数据读取、计算和输出工具。

## 执行规则

1. 为每个业务问题定义所需数据集、粒度、维度、指标、过滤条件和对比基准。
2. 明确指标依赖和计算顺序，避免同一指标在多个节点重复定义。
3. 为每个数据集指定至少一项可执行校验，包括完整性、唯一性、范围、单位或勾稽关系。
4. 只计划任务范围内的分析。领域配置中的推荐问题不得自动加入。
5. 标记需要确定性代码实现的计算和校验，不交给自然语言自由判断。
6. 图表和报告规划必须服务于分析目标，不能为了填满版面而增加内容。

## 输出格式

```json
{
  "status": "ready | blocked",
  "plan_id": "string",
  "datasets": [
    {
      "id": "string",
      "purpose": "string",
      "dimensions": [],
      "metrics": [],
      "filters": [],
      "comparison": {},
      "grain": "string",
      "validations": []
    }
  ],
  "execution_order": [],
  "chart_candidates": [],
  "report_sections": [],
  "blocking_issues": [],
  "assumptions": [],
  "next_node": "preparation | intake"
}
```

## 自检

- 每个计划数据集都能追溯到任务目标。
- 每项指标都有明确来源和定义。
- 每个数据集都有校验规则。
- 没有加入未经确认的业务范围。
- 执行顺序不存在循环依赖。

## 失败处理

任务配置无法支持分析计划时返回 `blocked`，并将恢复节点设为 `intake`。
