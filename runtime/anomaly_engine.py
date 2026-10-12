"""基于结构化白名单规则检测异常，不执行任意表达式。"""
from __future__ import annotations

import operator
import pandas as pd

_COMPARISONS = {
    "eq": operator.eq,
    "ne": operator.ne,
    "lt": operator.lt,
    "le": operator.le,
    "gt": operator.gt,
    "ge": operator.ge,
}


class AnomalyRuleError(ValueError):
    pass


def validate_rules(rules: list[dict], available_fields: set[str]) -> None:
    names = set()
    for rule in rules:
        name, field, operation = rule.get("name"), rule.get("field"), rule.get("operator")
        if not name or name in names:
            raise AnomalyRuleError(f"异常规则名称为空或重复：{name}")
        names.add(name)
        if field not in available_fields:
            raise AnomalyRuleError(f"异常规则 {name} 引用未知字段：{field}")
        if operation not in {*_COMPARISONS, "blank", "not_blank"}:
            raise AnomalyRuleError(f"异常规则 {name} 使用不支持的操作符：{operation}")
        if operation in _COMPARISONS and "value" not in rule:
            raise AnomalyRuleError(f"异常规则 {name} 缺少 value")


def evaluate_rules(frame: pd.DataFrame, rules: list[dict]) -> tuple[pd.Series, pd.Series]:
    validate_rules(rules, set(frame.columns))
    combined = pd.Series(False, index=frame.index)
    labels = pd.Series("", index=frame.index, dtype=object)
    for rule in rules:
        values, operation = frame[rule["field"]], rule["operator"]
        if operation == "blank": matched = values.fillna("").astype(str).str.strip().eq("")
        elif operation == "not_blank": matched = values.fillna("").astype(str).str.strip().ne("")
        else: matched = _COMPARISONS[operation](values, rule["value"]).fillna(False)
        combined |= matched
        labels = labels.mask(matched & labels.eq(""), rule["label"])
    return combined, labels
