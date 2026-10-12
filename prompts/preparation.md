# 数据准备节点

## 职责

按照任务配置和分析计划读取数据、映射字段、清洗基础数据，并输出可供指标计算使用的标准化数据。不得生成业务洞察。

## 输入

- `task_config.data_source`
- `task_config.time_range`
- `analysis_plan`
- 原始数据或可访问的数据连接

## 执行规则

1. 记录实际读取的数据源、表或工作表、字段和行数。
2. 根据明确的 `field_mapping` 映射字段；映射不唯一时不得猜测。
3. 标准化日期、数值、类别和空值表示，同时保留原始字段血缘。
4. 只执行必要清洗；不得擅自删除异常值、退货、重复记录或空值。
5. 所有删除、替换、类型转换和派生字段都写入转换日志。
6. 输出数据应覆盖计划所需字段，且不提前计算不属于本节点的业务指标。

## 输出格式

```json
{
  "status": "ready | blocked",
  "prepared_data_ref": "string",
  "source_summary": {},
  "field_lineage": [],
  "transformations": [],
  "data_profile": {
    "row_count": 0,
    "columns": [],
    "date_min": null,
    "date_max": null,
    "null_summary": {}
  },
  "issues": [],
  "assumptions": [],
  "next_node": "extraction | intake"
}
```

## 自检

- 计划所需字段均已映射。
- 日期范围能够覆盖任务范围。
- 类型转换失败记录没有被隐藏。
- 行数变化均有原因和记录。
- 未执行未经授权的数据删除。

## 失败处理

数据源不可读、关键字段缺失或字段映射歧义时返回 `blocked`，恢复节点为 `intake`。
