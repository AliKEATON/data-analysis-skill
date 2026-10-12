# 指标计算节点

## 职责

按照分析计划对已准备数据进行聚合和指标计算，输出结构化数据集。不得改变已确认的指标口径，也不得撰写业务建议。

## 输入

- `prepared_data`
- `analysis_plan.datasets`
- `task_config.metrics`
- `task_config.filters` 和 `task_config.comparison`

## 执行规则

1. 严格使用计划中的粒度、维度、指标、过滤条件和基准。
2. 派生指标必须记录公式、分子、分母、单位和异常处理方式。
3. 对零分母、缺失基期、重复键和无法转换的值显式标记，不得静默填充。
4. 每个数据集保留来源字段、过滤条件和计算步骤。
5. 计算应优先由确定性代码完成；不得用语言模型估算或补齐数值。
6. **必须产出归因/贡献度拆解（`attribution`）**：当 task_config.comparison 指定了基准期与报告期时，
   对 sales、profit 等核心指标，按主维度拆出每个分组对总量变化的贡献度并排序
   （`delta`、`contribution_rate = delta / total_change`）。
   - 目的是支撑「定位」：告诉下游「哪个维度/环节贡献最大」，而不是只列涨跌。
   - 无法计算（无基准期、总量变化为 0、维度缺失）时，`attribution` 置为空数组或该项 `contribution_rate=null`，
     不得编造贡献度。

## 输出格式

```json
{
  "status": "ready | blocked",
  "datasets": [
    {
      "id": "string",
      "grain": "string",
      "columns": [],
      "rows": [],
      "metric_definitions": [],
      "lineage": {},
      "attribution": [
        {
          "metric": "string",
          "group_dimension": "string",
          "baseline": "string",
          "target": "string",
          "total_change": 0,
          "items": [
            { "group": "string", "baseline_value": 0, "target_value": 0, "delta": 0, "contribution_rate": 0 }
          ]
        }
      ],
      "calculation_warnings": []
    }
  ],
  "execution_log": [],
  "blocking_issues": [],
  "next_node": "data_validation | preparation"
}
```

## 自检

- 输出数据集与分析计划逐项对应。
- 指标名称、定义和单位与任务配置一致。
- 没有未记录的过滤或数据补齐。
- 派生指标可以由已输出的输入项复算。
- 有基准期时 `attribution` 已给出按贡献度排序的分组明细，且贡献度与总量变化自洽（各项 delta 之和等于 total_change）。

## 失败处理

准备数据不足时返回 `blocked` 并回到 `preparation`；计算逻辑无法执行时记录具体指标和原因，不得输出伪造结果。
