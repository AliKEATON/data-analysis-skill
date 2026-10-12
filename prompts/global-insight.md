# 全局洞察节点

## 职责

跨多个已验证数据集整合事实，识别结构性变化、风险信号和可验证的业务含义，产出一份「结论清楚」的结论页材料，并决定哪些图表值得进入报告。

「结论清楚」有三条硬标准：

1. 读者 30 秒内知道该怎么办（结论 + 建议 + 影响量级在最前面）；
2. 每个数字都能回溯（口径、来源、时间范围、样本量、扫描范围写清楚）；
3. 结论与证据之间没有跳跃（显式区分「观察到」「推断」「无法判断」）。

## 输入

- `validated_datasets`（含 `attribution` 贡献度拆解，可直接用于定位）
- `local_insights`
- `charts`
- `task_config.analysis_goal` 和 `task_config.output.audience`
- `domain_profile` 中的分析问题和经验规则

## 写作规则

### headline（L1 一句话结论）

- 只写一句：什么指标、从多少变到多少、幅度、时间窗。
- 必须带 `evidence_refs`，引用真实存在的数据集/洞察/图表 id。

### 每条 global_insight 必须写成「结论页四小段」

按固定顺序填写四个字段，缺任何一段都视为未完成：

1. `phenomenon` 现象：什么指标、从多少变到多少、幅度、时间窗。
2. `localization` 定位：哪个维度/环节贡献最大，**给出贡献度排序**（可引用 `attribution`），不要只列涨跌。
3. `explanation` 解释：机制上说得通的假设，**显式标注「已验证」或「待验证」**；无法判断时写明「无法判断，需补充什么数据」。不得把相关性写成已证实的因果。
4. `action` 动作：`owner`（谁）+ `timing`（何时）+ `what`（做什么）+ `expected_impact`（预期影响量级）+ `verification`（怎么验证）。

### 其他约束

- 每条全局洞察至少连接两个不同指标、维度或期间；`evidence_refs` 至少两个。
- 数值必须有基准（环比 / 同比 / 目标 / 对照），没有基准的数字不构成结论。
- 小样本分组下必须写明样本量与置信度限制，`confidence` 取 high/medium/low。
- 不照抄局部洞察，不为了显得深刻而扩大结论。

## 正例

```json
{
  "id": "global_profit_quality",
  "phenomenon": "2026-07 较 2026-06：销售额由 1,105.46 万降至 1,039.45 万（-5.97%）、订单数 -18.70%，但计价利润由 176.80 万升至 199.18 万（+12.66%）。",
  "localization": "利润增长由利润率改善驱动而非规模：整体利润率 +3.17pp；贡献度上，欧美海外仓贡献利润占 65.8%，AL阿里业务部负利润明细占全部 57%。",
  "explanation": "推测减少低毛利/亏损订单带来盈利结构改善（待验证：需按 SKU 与订单类型下钻确认）；利润率提升是否可持续尚无法判断。",
  "action": {
    "owner": "经营分析组 + 欧美海外仓",
    "timing": "2 周内",
    "what": "对 AL阿里业务部负利润明细按 SKU/订单类型下钻，定位可裁撤的低效订单",
    "expected_impact": "若剔除该部分，预计整体利润率再有 0.5~1.0pp 改善空间",
    "verification": "下钻后对比剔除前后的计价利润与利润率，观察下月是否维持"
  },
  "evidence_refs": ["department_metrics_2026_06_2026_07", "insight_monthly_trend", "insight_department_risk"],
  "confidence": "medium"
}
```

## 反例

- 只写「销售额下降 5.97%，销售表现承压」——缺定位、缺解释、缺动作。
- 「销售额下降是因为投放收缩」——把因果写成已验证，未标注待验证。
- 「利润最高的是欧美海外仓」——只列涨跌，没有贡献度。

## 图表取舍

删除结论重复、证据较弱、图表类型不合适或超出报告容量的图表，并记录原因。删除图表不等于删除底层数据或洞察。

当 `task_config.output.include_charts=false` 时，`selected_chart_ids` 和图表设计决策保持为空，但仍可基于已验证数据和局部洞察生成全局洞察。

## 输出格式

```json
{
  "status": "ready | blocked",
  "headline": { "text": "string", "evidence_refs": [] },
  "global_insights": [
    {
      "id": "string",
      "phenomenon": "string",
      "localization": "string",
      "explanation": "string",
      "action": {
        "owner": "string",
        "timing": "string",
        "what": "string",
        "expected_impact": "string",
        "verification": "string"
      },
      "evidence_refs": [],
      "confidence": "high | medium | low"
    }
  ],
  "selected_chart_ids": [],
  "design_decisions": [
    { "chart_id": "string", "decision": "keep | remove", "reason": "string" }
  ],
  "issues": [],
  "next_node": "reporting | local_insight | data_validation"
}
```

## 自检

- headline 是否一句话说清「指标+数值变化+时间窗」。
- 每条洞察的四小段是否齐全，`action` 五要素是否完整。
- 定位是否给出贡献度排序，而不是只列涨跌。
- 事实、推断、待验证假设是否明确区分。
- 所有量化信息都能追溯到数据集，`evidence_refs` 无幽灵引用。

## 失败处理

证据不足或局部洞察冲突时返回 `blocked`，恢复节点为 `local_insight` 或 `data_validation`。
