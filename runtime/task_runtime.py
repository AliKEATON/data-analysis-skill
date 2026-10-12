from __future__ import annotations
from dataclasses import dataclass
import pandas as pd


def time_bounds(spec: dict) -> tuple[pd.Timestamp, pd.Timestamp, str]:
    granularity = spec.get("granularity", "month")
    formats = {"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}
    if granularity not in formats:
        raise ValueError(f"不支持的时间粒度：{granularity}")
    start_text, end_text = spec["start"], spec.get("end") or spec["start"]
    start = pd.to_datetime(start_text, format=formats[granularity])
    end_period = pd.to_datetime(end_text, format=formats[granularity])
    if end_period < start:
        raise ValueError("time_range.end 不能早于 start")
    if granularity == "day":
        end = end_period + pd.Timedelta(days=1)
        slug = f"{start:%Y-%m-%d}_{end_period:%Y-%m-%d}"
    elif granularity == "month":
        end = end_period + pd.offsets.MonthBegin(1)
        slug = f"{start:%Y-%m}_{end_period:%Y-%m}"
    else:
        end = end_period + pd.offsets.YearBegin(1)
        slug = f"{start:%Y}_{end_period:%Y}"
    return start, end, slug


@dataclass(frozen=True)
class MetricPlan:
    definitions: dict
    sales: str
    profit: str
    order: str
    base: dict
    derived: dict

    @classmethod
    def from_task(cls, task: dict) -> "MetricPlan":
        definitions = {item["name"]: item for item in task.get("metrics", [])}
        by_role = {}
        for name, spec in definitions.items():
            role = spec.get("role")
            if role and role != "other":
                if role in by_role:
                    raise ValueError(f"指标角色重复：{role}")
                by_role[role] = name
        sales, profit, order = by_role.get("sales"), by_role.get("profit"), by_role.get("order_count")
        if not sales or not profit or not order:
            raise ValueError("任务必须分别配置 sales、profit、order_count 指标角色")
        return cls(definitions, sales, profit, order,
                   {n: s for n, s in definitions.items() if s.get("source_field")},
                   {n: s for n, s in definitions.items() if s.get("formula")})

    @property
    def order_source(self) -> str: return self.definitions[self.order]["source_field"]

    @property
    def numeric_sources(self) -> set[str]:
        return {s["source_field"] for s in self.base.values() if s.get("aggregation") != "count_distinct"}
