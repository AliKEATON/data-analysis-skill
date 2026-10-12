# 图表生成节点

## 职责

根据已验证数据和局部洞察选择适当图表并生成图表配置。图表用于表达已经存在的分析证据，不重新计算或改写指标。

## 输入

- `validated_datasets`
- `local_insights`
- `task_config.output`
- `config/defaults.yaml` 中的 `charts` 规范（配色 / 坐标轴 / 标注 / 辅助线 / 选型）

## 图表类型选择规则（必须遵守）

| 数据特征 | 应选类型 | 禁用 |
| --- | --- | --- |
| 时序数据 | 折线图 / 面积图（`line` / `area`） | 饼图 |
| 类别对比 ≤ 5 类 | 纵向柱状图（`vertical_bar`） | 饼图 |
| 类别对比 ≥ 6 类 | 横向条形图（`horizontal_bar`） | 饼图 |
| 量 + 率同时展示 | 双 Y 轴组合（`dual_axis`，柱 + 线） | 单轴混排 |
| 多维度趋势对比 | 多折线 | 堆叠柱 |

当 `task_config.output.include_charts=false` 时，不生成图表，返回 `status=ready`、空 `charts`，并进入 `synthesis`。

## 配色规范（必须严格遵守）

- 主色系：深蓝 `#003366`、中蓝 `#0073CF`、浅蓝 `#5AB4E0`。
- 强调色：红 `#E63329`（负向/警示）、橙 `#F7941D`（均值线）、绿 `#00A551`（正向）。
- **禁止使用规范以外的颜色。** `option.palette` 只能取自以上六个十六进制值。

## 坐标轴规范

- 柱状图 Y 轴从 0 开始。
- 折线图可压缩，但必须标注「非零起点」（写入 `option.axis.note`）。
- Y 轴最大值 = 数据最大值 × 1.2（留标注空间），即 `option.axis.max`。
- 双 Y 轴：右轴 min/max 必须独立显式设置（写入 `option.axis.right`），禁止依赖自动缩放。

## 数据标注规范

- 最多标注 3 个关键点：最新值、峰值、对比基准（`option.labels`，`kind` 取 `latest | peak | baseline`）。
- 字体：`fontWeight='bold'`、`fontSize=12`。
- 数据点超过 20 时关闭逐点标注（`labels` 置空）。

## 辅助线规范

- 均值线：橙色虚线 `#F7941D`，标签形如「均值 XX%」。
- 目标线：红色点线 `#E63329`，标签形如「目标 XX」。
- 均写入 `option.aux_lines`（`kind` 取 `average | target`）。

## 输出格式

```json
{
  "status": "ready | blocked",
  "charts": [
    {
      "id": "string",
      "dataset_id": "string",
      "insight_id": "string",
      "chart_type": "line | area | vertical_bar | horizontal_bar | dual_axis",
      "title": "string",
      "subtitle": "string",
      "encoding": {},
      "option": {
        "palette": ["#0073CF", "#5AB4E0"],
        "axis": { "min": 0, "max": 0, "from_zero": true, "note": "" },
        "labels": [
          { "x": "2026-07", "value": 0, "kind": "latest" }
        ],
        "aux_lines": [
          { "kind": "average", "color": "#F7941D", "style": "dashed", "value": 0, "label": "均值 0.00" }
        ],
        "unit": "CNY"
      },
      "accessibility_summary": "string",
      "validation": []
    }
  ],
  "issues": [],
  "next_node": "synthesis | charting | data_validation | local_insight"
}
```

图表标题优先表达结论，而不是描述维度（如「欧美海外仓贡献 127.9% 利润增量」优于「部门利润排名」）。

## 自检

- 图表类型与数据结构匹配，且符合选型规则。
- 配色全部来自规范调色板，未出现规范外颜色。
- 柱状图从 0 开始；折线压缩已标注「非零起点」；Y 轴最大值为数据最大值 × 1.2。
- 数据标注不超过 3 个关键点，超过 20 个数据点时已关闭逐点标注。
- 均值线/目标线的颜色与线型符合规范，并带标签。
- 标题中的数字与数据集一致，不使用图表类型制造不存在的趋势。

## 失败处理

输入数据或洞察不一致时返回 `blocked` 并回到 `data_validation` 或 `local_insight`；图表配置自身不合格时留在 `charting` 节点修正。
