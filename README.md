# 可复用数据分析 Skill

这是一个面向结构化数据分析的可复用 Skill / Agent 工作流骨架。项目借鉴文章中的分层思路，将“业务条件”从提示词中抽离为运行时任务配置，将数据准备、指标计算、校验、洞察、制图和报告组装拆成职责单一的节点。

当前版本已经完成 **Skill、Harness、通用 runtime 执行引擎、本地 Agent 推理接入与首个真实订单案例的全链路复跑**：具备任务配置契约（含 date_field、derived_dimensions、primary_dimension、metric.role、label 等字段）、领域配置、节点提示词、工作流路由、12 个 JSON Schema、跨节点运行校验、执行前预检、通用 10 节点流水线执行器、8 套回归测试套件（共 65 项）、本地 Agent 推理注入层（未提供推理时显式阻塞、禁止模板冒充洞察）、归因贡献度拆解、明细下钻通道、金字塔式结论页报告、图表规范（配色/坐标轴/标注/辅助线/选型）、报告规范（布局/剔除/撰写/尺寸/字体）和性能基准脚本。

当前执行器已支持任意符合 task.schema.json 契约的本地文件任务从原始 CSV/XLSX/Parquet 生成标准化数据（prepared parquet）、基础指标聚合（sum / avg / max / min / count / count_distinct）、安全派生公式（+-*/ AST 白名单）、配置驱动异常检测、维度/月度/月度×主实体多重汇总、异常清单落盘、html/markdown/xlsx 三种报告、execution_snapshot 与 10 节点运行清单。订单任务保持向后兼容（prepared_orders_ 命名、department_summary.csv、amt/calc_final_profit 口径等）。

## 核心设计

项目将数据分析拆成三层：

```text
静态 Skill 规则
  ├─ 节点职责
  ├─ 输出契约
  ├─ 校验规则
  └─ 工作流路由

领域配置
  ├─ 候选维度
  ├─ 候选指标
  ├─ 指标口径
  └─ 领域问题与风险信号

运行时任务配置
  ├─ 本次分析目标
  ├─ 数据源和字段映射
  ├─ 时间范围
  ├─ 分析维度和指标
  ├─ 过滤条件与对比基准
  └─ 输出格式和使用对象
```

固定的是执行机制和质量规则，动态的是每次分析的业务条件。缺少时间范围、指标口径、维度、对比基准或字段映射时，Agent 必须先提问，不能自行猜测。

## 项目架构

```text
super-data-analysis/
├── SKILL.md                         # Skill 入口与总原则
├── README.md                        # 项目说明
├── config/
│   ├── task.schema.json             # 运行时任务配置契约
│   ├── defaults.yaml                # 通用默认规则
│   └── domains/
│       ├── generic.yaml             # 通用领域配置
│       └── sales.yaml               # 销售经营分析候选配置
├── prompts/                         # 节点提示词，按需加载
│   ├── intake.md                    # 任务澄清
│   ├── planner.md                   # 分析计划
│   ├── preparation.md               # 数据准备
│   ├── extractor.md                 # 指标计算
│   ├── validator.md                 # 数据校验
│   ├── local-insight.md             # 局部洞察
│   ├── chart.md                     # 图表生成
│   ├── global-insight.md            # 全局洞察
│   ├── report.md                    # 报告组装
│   └── final-review.md              # 最终审查
├── schemas/                         # 节点和运行清单的 JSON Schema
│   ├── intake-result.schema.json
│   ├── analysis-plan.schema.json
│   ├── prepared-data.schema.json
│   ├── datasets.schema.json
│   ├── validation-report.schema.json
│   ├── local-insights.schema.json
│   ├── charts.schema.json
│   ├── synthesis.schema.json
│   ├── report.schema.json
│   ├── final-package.schema.json
│   └── run-manifest.schema.json
├── runtime/                         # 通用运行时引擎（订单领域零硬编码）
│   ├── source_reader.py             # CSV/XLSX/XLS/Parquet 读取 + 列映射 + 派生维度
│   ├── safe_formula.py              # 派生指标 AST 白名单解释器
│   ├── metric_engine.py             # 多维度聚合（sum/avg/max/min/count）+ 去重计数 + 派生计算
│   ├── anomaly_engine.py            # 配置驱动异常规则（blank/eq/lt/...）
│   ├── task_runtime.py              # time_bounds + MetricPlan 角色解析
│   └── node_hooks.py                # NodeHooks Protocol + LocalAgentHooks（本地 Agent 推理注入）
├── workflow/
│   └── pipeline.yaml                # 节点顺序、成功边和失败回退
├── scripts/
│   ├── __init__.py                    # Python 包标识（支持 tests 导入 execute_pipeline）
│   ├── validate_artifact.py           # 单节点产物校验
│   ├── validate_pipeline.py           # 工作流引用校验
│   ├── validate_run.py                # 端到端运行清单校验
│   ├── validate_task_runtime.py       # 执行前任务和源字段预检（动态 REQUIRED）
│   ├── run_analysis.py                # 统一任务执行入口
│   ├── execute_pipeline.py            # 通用 10 节点流水线执行器（主入口）
│   ├── safe_formula.py                # 公式 AST 白名单（供 validate_task_runtime 导入）
│   ├── generate_html_report.py        # 报告生成器（html/markdown/xlsx 可插拔）
│   ├── build_approved_run.py          # 10 节点运行清单生成（支持 hooks 注入）
│   └── benchmark.py                   # 性能基准（耗时 + Python 峰值内存）
├── tests/
│   ├── test_validators.py            # Harness 回归（9 类异常链路 + 黄金勾稽）
│   ├── test_source_reader.py         # 3 种文件格式语义一致性
│   ├── test_time_bounds.py           # 时间边界左闭右开
│   ├── test_runtime_quality.py       # 真实 Parquet 金额勾稽 + 异常标签分布
│   ├── test_execute_pipeline.py      # 通用流水线端到端回归
│   ├── test_node_hooks.py            # LocalAgentHooks 基类阻塞与覆写实现
│   ├── test_metric_engine.py         # 聚合类型扩展（avg 跨 chunk 加权）
│   ├── test_report_writer.py         # Markdown/XLSX 报告渲染
│   └── fixtures/
│       └── order_analysis_2026-06_07.expected.json
└── references/
    └── architecture.md              # 架构、契约和数据血缘说明
```

## 节点职责

| 节点 | 作用 | 主要输入 | 主要输出 |
| --- | --- | --- | --- |
| `intake` | 澄清需求，不执行分析 | 用户需求、领域配置 | `task_config` 或澄清问题 |
| `planning` | 制定数据集、计算和校验计划 | 任务配置、领域配置 | `analysis_plan` |
| `preparation` | 读取数据、字段映射、基础清洗 | 数据源、分析计划 | 标准化数据引用、字段血缘 |
| `extraction` | 执行指标计算 | 准备后的数据、分析计划 | 指标数据集 |
| `data_validation` | 检查完整性、口径、单位和勾稽关系 | 指标数据集、任务配置 | 校验报告 |
| `local_insight` | 生成单数据集事实解读 | 已验证数据集 | 局部洞察 |
| `charting` | 选择图表并生成配置 | 数据集、局部洞察 | 图表配置 |
| `synthesis` | 跨数据集整合洞察并取舍图表 | 数据集、洞察、图表 | 全局洞察、设计决策 |
| `reporting` | 组装最终报告 | 图表、全局洞察、任务配置 | 报告产物 |
| `final_review` | 最终审查和交付 | 全部产物、任务配置 | 最终交付包 |

## 数据流转

正常数据流如下：

```text
用户自然语言需求
        │
        ▼
任务澄清 intake
        │ 缺少关键条件
        ├──────────────► 向分析师提问
        │                         │
        │                         └── 补全后重新进入 intake
        ▼
task_config
        ▼
分析计划 planning
        ▼
数据准备 preparation
        ▼
指标计算 extraction
        ▼
数据校验 data_validation
        │ 失败
        ├──────────────► 返回 preparation 或 extraction
        ▼
局部洞察 local_insight
        ▼
图表生成 charting
        │ 失败
        ├──────────────► 返回 charting、data_validation 或 local_insight
        ▼
全局洞察 synthesis
        ▼
报告组装 reporting
        ▼
最终审查 final_review
        │ 失败
        ├──────────────► 返回受影响的上游节点
        ▼
complete
```

每个节点都通过结构化 JSON 传递结果。节点提示词中的 JSON 示例只用于帮助模型理解，真正的格式约束由 `schemas/` 中的 JSON Schema 执行。

## 任务配置

每次分析的动态条件由 `config/task.schema.json` 定义。主要字段包括：

```json
{
  "analysis_goal": "比较2026年6-7月三部门销售额与利润差异",
  "data_source": {
    "type": "file",
    "location": "data/orders.csv",
    "field_mapping": { "opration_date": "日期", "amt": "销售额", "group_first": "一级部门" },
    "date_field": "opration_date",
    "derived_dimensions": [{ "name": "department", "type": "concat", "sources": ["group_first", "group_second"] }]
  },
  "time_range": { "start": "2026-06", "end": "2026-07", "granularity": "month" },
  "dimensions": ["department", "country_code", "order_type"],
  "metrics": [
    { "name": "amt", "role": "sales", "label": "销售额", "source_field": "amt", "aggregation": "sum" },
    { "name": "profit_margin", "role": "margin", "label": "利润率", "formula": "calc_final_profit / amt", "zero_denominator": "null" }
  ],
  "output": { "format": "html", "include_charts": true, "primary_dimension": "department" }
}
```

- 新增关键字段说明：
  - `data_source.date_field` 日期逻辑列名（来自 field_mapping 的 key），缺失时向下兼容为 `opration_date`；
  - `data_source.derived_dimensions[]` 读取后派生逻辑维度（支持 `concat` 多列拼接 / `copy` 单字段重命名）；
  - `metrics[].role` 枚举（sales/profit/order_count/margin/quantity/refund/other），用于运行时抽取主口径；
  - `metrics[].source_field + aggregation` vs `metrics[].formula + zero_denominator` 二选一；
  - `output.primary_dimension` 指定月度×主实体交叉汇总的主维度列名。

时间范围、维度、指标和业务口径属于运行时配置，不应该写死在通用 Skill 中。销售领域配置只提供候选项和澄清问题，不能替代分析师确认。

## 依赖组件

### 运行环境

- Python 3.10 或更高版本，脚本使用了现代类型标注语法。
- UTF-8 文件编码。
- PowerShell 或其他能够执行 Python 命令的终端。

### Python 依赖

当前项目使用以下外部 Python 包：

- `jsonschema`：校验任务配置、节点产物和运行清单。
- `PyYAML`：读取 `defaults.yaml`、领域配置和 `pipeline.yaml`。
- `pandas`、`numpy`：读取、清洗和聚合明细数据。
- `pyarrow`：读取和写入 Parquet。
- `openpyxl`：读取 XLSX 文件。

以下能力使用 Python 标准库，不需要额外安装：

- `json`：读写 JSON。
- `pathlib`：路径处理。
- `datetime`：时间范围校验。
- `zipfile`：基础 XLSX 文件结构校验。
- `unittest`：回归测试。

安装运行依赖：

```powershell
python -m pip install -r requirements.txt
```

旧版 `.xls` 文件需要额外安装可选依赖 `xlrd`。当前文件读取层支持 CSV、XLSX、XLS 和 Parquet；数据库和 API 数据源尚未实现。

## 校验与测试

在项目根目录执行：

```powershell
# 检查工作流节点、提示词和 Schema 引用
python scripts/validate_pipeline.py

# 校验单个节点产物
python scripts/validate_artifact.py schemas/intake-result.schema.json path/to/intake.json

# 校验完整运行清单、跨节点引用和交付文件
python scripts/validate_run.py path/to/manifest.json

# 运行回归测试
python -m unittest discover -s tests -v

# 执行已确认订单案例（重新读取原始 CSV，注入本地 Agent 推理钩子）
python scripts/run_analysis.py `
  --task my_case/task.json `
  --output outputs/order_analysis_2026-06_07 `
  --hooks my_case/agent_reasoning_hooks.py `
  --validate

# 执行前预检（检查 task.json 契约 + 数据源列头 + 公式安全性）
python scripts/validate_task_runtime.py my_case/task.json

# 直接使用通用流水线（不通过 run_analysis 包装），结果与订单案例一致
python scripts/execute_pipeline.py `
  --task my_case/task.json `
  --output outputs/order_analysis_2026-06_07 `
  --hooks my_case/agent_reasoning_hooks.py

# 性能基准（测量流水线端到端耗时与 Python 峰值内存）
python scripts/benchmark.py --runs 1
```

当前回归测试覆盖（共 65 项）：

- 合法的有图分析链路
- 合法的无图分析链路
- 澄清结果矛盾
- 上游失败但伪造下游成功
- 计划增加未确认指标
- 幽灵数据集、洞察和图表引用
- 空交付包
- 空报告、错误扩展名和非法 HTML
- 无图任务夹带图像
- 多个阻断问题的恢复路由冲突
- 真实订单案例源文件 SHA-256、行数与范围行数
- 总体和月度销售额、计价利润、订单数、利润率及负利润行数
- 18 个“月份 × 部门”组合的去重订单数
- HTML 报告、运行清单和最终审查中的已确认指标口径
- 通用流水线 execute_pipeline 端到端回归（同金额、同产出文件名）
- 派生维度 concat/copy 生成正确性与缺失来源列报错
- LocalAgentHooks 基类对推理节点抛 AgentReasoningRequired，覆写后可产出推理结果
- 聚合类型扩展（avg 跨 chunk 加权、max/min/count）
- Markdown / XLSX 报告渲染正确性

真实案例的轻量黄金基线位于 `tests/fixtures/order_analysis_2026-06_07.expected.json`。它只保存可审计的控制总量和源文件哈希，不复制 696,885 行原始数据。业务确认导致数据或口径发生变化时，应先审阅差异，再更新该基线；不能仅为使测试通过而覆盖预期值。

## 端到端运行清单

完整运行使用 `schemas/run-manifest.schema.json` 描述任务配置和十个节点产物的位置：

```json
{
  "task_config": "task.json",
  "artifacts": {
    "intake": "intake.json",
    "planning": "planning.json",
    "preparation": "preparation.json",
    "extraction": "extraction.json",
    "data_validation": "data_validation.json",
    "local_insight": "local_insight.json",
    "charting": "charting.json",
    "synthesis": "synthesis.json",
    "reporting": "reporting.json",
    "final_review": "final_review.json"
  }
}
```

`validate_run.py` 会检查：

- 每个节点产物是否符合自己的 Schema；
- 所有节点是否成功，避免上游失败后伪造完整交付；
- 计划数据集是否完整覆盖任务配置；
- 局部洞察、图表和全局洞察是否引用真实对象；
- 报告是否存在、非空、格式正确且列入最终交付物；
- `include_charts=false` 时是否真的没有图表；
- 相对路径是否以运行清单所在目录为基准解析。

## 订单案例运行产物

已确认案例的输入配置位于 `my_case/task.json`，本地 Agent 推理钩子位于 `my_case/agent_reasoning_hooks.py`，输出目录为 `outputs/order_analysis_2026-06_07/`，主要产物包括：

- `management_report.html`：经营分析报告；
- `prepared_orders_2026_06_2026_07.parquet`：按实际分析期间命名的标准化明细；
- `monthly_summary.csv`、`department_summary.csv`：汇总指标；
- `anomaly_orders.csv`：异常明细；
- `run/manifest.json` 和 `run/final_review.json`：十节点运行清单和最终审查。

本案例固定使用 `amt` 作为人民币销售额、`calc_final_profit` 作为计价利润，订单数按 `order_number` 去重。执行前会校验主指标、字段映射、源文件存在性、时间范围和 HTML/图表配置。

指标可通过 `source_field + aggregation` 定义基础汇总，或通过 `formula + zero_denominator` 定义派生指标。公式由 AST 白名单解释器执行，仅允许已声明指标、数值常量、括号、`+ - * /` 和一元正负号；函数调用、属性访问、下标和其他 Python 表达式会在预检阶段被阻断。

## 当前 MVP 边界

已完成：

- 可复用 Skill 入口和中文说明
- 通用及销售领域配置
- 任务澄清和动态参数模型（date_field、derived_dimensions、metric.role、label 等）
- 十节点提示词工作流
- 12 个 JSON Schema
- 工作流引用校验
- 单节点和端到端运行校验
- 8 套 Harness 回归测试（65 项，含 9 类异常链路 + 黄金勾稽）
- 配置驱动的基础指标聚合（sum / avg / max / min / count / count_distinct）和安全派生指标公式（AST 白名单 +-*/）
- 真实订单案例黄金回归基线
- **通用 10 节点流水线执行器（execute_pipeline.py）**：支持任意符合 task.schema.json 的任务
- **runtime 引擎全面去硬编码**：date_field 参数化、derived_dimensions（concat/copy）、primary_dimension、负利润列名动态计算
- **NodeHooks + LocalAgentHooks**：推理节点固定由本地 Agent 产出，未提供时抛 AgentReasoningRequired，禁止模板冒充洞察
- **本地 Agent 推理参考实现**：`my_case/agent_reasoning_hooks.py`
- **可插拔报告模板**：html / markdown / xlsx 三种输出格式，按 output.format 分发
- **归因贡献度拆解**：`extractor` 产出 `attribution`，按主维度给出各分组对总量变化的 `delta` 与 `contribution_rate` 排序（`runtime/attribution.py`）
- **明细下钻通道**：`runtime/drilldown.py` 提供 `drill.top_by(dimension, metric, n)`，推理节点可按任意维度主动下钻，而非只复述汇总
- **金字塔式结论页**：`synthesis` 产出 `headline` + 每条洞察的「现象/定位/解释/建议动作」四小段，报告渲染层在最前面呈现「结论摘要」
- **图表规范**：配色/坐标轴/标注/辅助线/选型集中为 `config/defaults.yaml` + `runtime/chart_style.py` 单一事实源；`charts` 契约强制 `option`（调色板、Y 轴最大值×1.2、标注≤3、均值/目标辅助线），渲染层输出 SVG 折线图与合规柱图
- **报告规范**：布局（5 张 KPI、两列 6:4、每 Tab ≤4 图）、主动图表剔除（重复/差异<5%/超量）、结论撰写（发现+量化支撑+业务含义）、容器尺寸、备注字体，集中为 `runtime/report_style.py` 并在渲染层执行
- **性能基准脚本（benchmark.py）**：测量流水线端到端耗时与 Python 峰值内存

## 已知边界（尚未验证或实现）

- **通用性仅单领域验证**：代码已完全去硬编码，但只有销售订单跑过真实数据，换行业尚未经实战检验。
- **本地 Agent 推理已落地**：推理节点（local_insight / charting / synthesis）固定由本地 Agent 产出，未覆写时抛 `AgentReasoningRequired` 显式阻塞，绝不用模板冒充洞察。
- **性能为单机单案例数据**：69.7 万行端到端约 50 秒，仅测过此量级，更大数据量下的表现未知。
- **数据库 / API 数据源未接入**：当前仅支持本地 CSV/XLSX/XLS/Parquet，schema 已预留枚举但适配器未实现。

因此，当前版本已具备通用执行、本地 Agent 推理接入、多聚合类型、多报告格式和性能基准能力，runtime 层与报告层均无订单领域硬编码，具备跨领域扩展的完整接口基础。

## 后续建议

1. 用本地 Agent 对更多行业案例验证洞察/合成质量，并评估长链路推理的成本与耗时。
2. 接入第二个行业案例，验证通用性与多领域性能对比。
3. 评估大数据集下的内存优化（当前 69.7 万行端到端约 50 秒）。
4. 引入上下文裁剪、记忆和子 Agent 调度策略，优化长链路推理效率。
