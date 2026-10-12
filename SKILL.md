---
name: reusable-data-analysis
description: 构建和运行可复用的数据分析工作流，负责任务澄清、业务条件参数化、数据校验、基于证据的洞察生成和可审计报告组装。
metadata:
  short-description: 可复用的数据分析工作流
---

# 可复用数据分析

当用户要求分析结构化数据、定义分析流程、生成图表或报告，或将领域分析规则转化为可复用 Agent 工作流时，使用本 Skill。

## 核心原则

保持执行机制稳定，将业务特有条件作为任务配置传入。当缺少时间范围、指标定义、分析维度、对比基准或字段映射，且这些条件可能影响结果时，不得自行猜测。

## 工作流

遵循 `workflow/pipeline.yaml`。标准顺序如下：

1. 澄清需求并生成通过校验的任务配置。
2. 根据任务配置和适用的领域配置生成分析计划。
3. 执行数据准备和指标计算。
4. 校验数据完整性、指标定义、单位和勾稽关系。
5. 生成基于证据的局部洞察。
6. 生成图表并校验图表语义。
7. 汇总跨维度洞察并组装所需报告。
8. 执行最终审查，返回产物、假设和校验结果。

## 执行模式

**推理节点固定由「本地 Agent」产出，不接入外部大模型，禁止用固定模板冒充洞察。**

节点职责分工：

- **确定性节点**：`preparation`（读取/清洗）、`extraction`（指标聚合）、`data_validation`（勾稽计算）。
  由代码完成（`scripts/execute_pipeline.py`），本地 Agent 不参与这类精确计算。
- **推理节点**：`intake`、`planning`、`local_insight`、`charting`、`synthesis`、`final_review`。
  必须由本地 Agent（当前对话模型或子 Agent）读取 `prompts/*.md` 后亲自产出。

本地 Agent 的接入方式：

1. **直接执行**：Agent 读取对应 `prompts/*.md`，亲自产出节点 JSON，再交校验器把关。
2. **注入式实现**：继承 `runtime/node_hooks.py` 的 `LocalAgentHooks`，覆写 `run_local_insight`、
   `run_charting` 与 `run_synthesis`，把 Agent 产出的洞察与图表注入 `execute_pipeline.run(..., hooks=...)`。
   参考实现：`my_case/agent_reasoning_hooks.py`。
3. **命令行注入**：`python scripts/execute_pipeline.py --task <task.json> --output <dir> --hooks <hooks文件或模块路径>`；
   hooks 入口需提供 `build_hooks()` 返回 `LocalAgentHooks` 实例。未传 `--hooks` 时回退基类，推理节点会抛异常。

**未提供推理产出时的行为**：`LocalAgentHooks` 基类会在推理节点抛 `AgentReasoningRequired`，
**绝不回退模板、绝不产出假洞察**。此时请补全推理节点产出后重跑。

## 信息缺失时的处理

当必填信息缺失或含义存在歧义时，返回 `need_clarification` 并提出聚焦问题。不得默默为业务含义编造默认值。默认值只能用于展示或格式化，并且必须记录到 `assumptions`。

## 数据契约

任务输入必须符合 `config/task.schema.json`。每个节点的输出必须符合 `workflow/pipeline.yaml` 中 `output_schema` 指向的 JSON Schema。生成或修改节点产物后，运行 `scripts/validate_artifact.py` 校验；修改工作流、提示词或 Schema 后，运行 `scripts/validate_pipeline.py` 校验引用关系；完整运行结束后，使用 `scripts/validate_run.py` 检查任务范围、跨节点引用和交付物真实性。

## 质量规则

- 区分事实、计算结果、局部洞察和业务建议。
- 每条洞察都必须可以追溯到输入数据，并包含量化依据。
- 保持节点职责单一，通过结构化数据在节点间传递结果。
- 对可以用代码表达的检查，使用确定性的校验和路由。
- 校验失败时返回指定的恢复节点，不得继续使用未经验证的数据。

### 「结论清楚」的三条硬标准

1. 读者 30 秒内知道该怎么办：报告的 L1 结论页给出「一句话结论 + 支撑 + 建议动作」，
   由 `synthesis` 的 `headline` 与每条 `global_insight` 的「现象/定位/解释/建议动作」四小段驱动，
   报告渲染层直接呈现，不再依赖固定 Tab。
2. 每个数字都能回溯：`extractor` 必须产出 `attribution`（按主维度拆出各分组的
   `delta` 与 `contribution_rate` 并排序），定位结论必须引用贡献度而非只列涨跌。
3. 结论与证据之间没有跳跃：`explanation` 必须显式标注「已验证/待验证/无法判断」，
   小样本分组需给出样本量与 `confidence`。

推理节点在做「定位」时，除引用 `attribution` 外，还可通过下钻通道
`drill.top_by(dimension, metric, n)` 在标准化明细上按 sku / country_code / order_type 等任意维度
主动下钻（见 `runtime/drilldown.py`），用于发现子群体层面的问题，而不是停留在汇总复述。

### 图表规范

`charting` 节点必须遵守 `config/defaults.yaml` 的 `charts` 规范，并写入 `option`：

1. 选型：时序→折线/面积；类别 ≤5→纵向柱；类别 ≥6→横向条形；量+率→双 Y 轴；均禁用饼图。
2. 配色：仅允许 `#003366 / #0073CF / #5AB4E0 / #E63329 / #F7941D / #00A551`，禁止规范外颜色。
3. 坐标轴：柱状图从 0 开始；折线可压缩但须标注「非零起点」；Y 轴最大值 = 数据最大值 × 1.2；双轴右轴独立设置。
4. 标注：最多 3 个关键点（最新值/峰值/对比基准），`fontWeight=bold`、`fontSize=12`；数据点 >20 时关闭逐点标注。
5. 辅助线：均值线橙色虚线 `#F7941D`、目标线红色点线 `#E63329`，均带标签。

### 报告规范

`reporting` 节点必须遵守 `config/defaults.yaml` 的 `report` 规范：

**报告结构问题驱动（不套用固定板块）**：主体按分析问题逐条组织，每个问题一个章节，内部顺序为
「标题 → 问题叙述 → 证据图表 → 来源/口径 → 结论卡片」；问题数量与内容由数据与洞察决定。
固定只保留顶部 5 个 KPI 概览与文末「附录：明细、方法与追溯」。

1. 布局：每个 Tab 顶部 5 个 KPI（最新期主指标 / 环比 / 利润率 / TOP 主实体 / TOP 次维度）；
   正文按「问题模块」成行，**一个问题模块独占一行**（不同模块不得挤在同一行），
   同行内只放该模块自己的证据图表且**同行等高**（列数与比例取 `charts.max_columns_per_screen` 与 `report.chart_columns`），
   **时序图（`encoding.x = analysis_month`）独占整行**；每 Tab 最多 4 张图。
2. 图表剔除（主动执行）：同维度同时间范围重复、组间差异 < 5%、超过 4 张，任一条满足即删除并记录原因。
3. 结论撰写：固定为 `发现 + 量化支撑 + 业务含义`；禁止只有现象的描述句与无测量标准词。
4. 容器尺寸：双 Y 轴 320px（最小 280）；月度折线 260px（48 点 300px）；横向条形 ≥7 项按每项 40px；
   以上为行内图高基准，同行取较大值等高，不做全局拉平。
5. 备注字体：图表来源 11px `#888`（标题下方）、口径说明 11px `#666`（Tab 底部）、图表结论卡片 12px `#444`（蓝色左边框）。
6. 摘要：开头「结论摘要」只给一句话结论 + 本次问题清单（带 `#q1`…`#qN` 跳转锚点），
   不重复各洞察的「现象/定位/解释/建议动作」；这些只在正文对应问题的结论卡片出现一次。

规则由 `runtime/report_style.py` 提供（`prune_charts` / `container_height` / `note_style`），报告渲染器据此执行。

## 案例与目录约定

每个分析「案例」使用一个独立目录，便于复用与隔离：

```
cases/<case-id>/
├── task.json                   # 任务配置（须符合 config/task.schema.json）
└── agent_reasoning_hooks.py    # 本地 Agent 推理钩子（含 build_hooks()，覆写 run_local_insight / run_charting / run_synthesis）
```

- `<case-id>` 使用稳定、可读的短标识（如 `ali-expense`、`order-analysis-2026-06_07`），不要临时起名。
- 产物统一输出到 `outputs/<case-id>/`，不要散落到项目根目录。
- `my_case/` 是内置参考案例（保留为 SKILL 示例与各脚本默认 `--task` 路径）；**新案例一律放 `cases/<case-id>/`**。
- 启动与校验：

```powershell
python scripts/execute_pipeline.py `
  --task cases/<case-id>/task.json `
  --output outputs/<case-id> `
  --hooks cases/<case-id>/agent_reasoning_hooks.py

python scripts/validate_run.py outputs/<case-id>/run/manifest.json
```

## 配套文件

- `config/task.schema.json`：动态任务配置契约。
- `config/defaults.yaml`：展示和校验默认规则，含 `charts` 图表规范（配色/坐标轴/标注/辅助线/选型）。
- `runtime/chart_style.py`：图表规范唯一事实源的加载与规则函数（选型、坐标轴、标注、辅助线、配色校验）。
- `runtime/report_style.py`：报告规范唯一事实源的加载与规则函数（图表剔除、容器尺寸、备注字体）。
- `workflow/pipeline.yaml`：节点顺序、校验门禁和恢复路径。
- `references/architecture.md`：静态规则、领域配置和运行时任务参数的分层说明。
- `schemas/`：各节点输出的机器可读契约。
- `scripts/validate_artifact.py`：校验单个节点 JSON 产物。
- `scripts/validate_pipeline.py`：校验工作流、提示词和 Schema 引用。
- `scripts/validate_run.py`：校验完整运行的数据流、引用关系和交付物。

## 按需加载

- 默认读取 `config/domains/generic.yaml`；明确属于销售经营分析时，再读取 `config/domains/sales.yaml`。
- 执行某个工作流节点前，只读取 `pipeline.yaml` 中该节点 `prompt` 指向的提示词文件。
- 不要一次性加载全部节点提示词或所有领域配置。
