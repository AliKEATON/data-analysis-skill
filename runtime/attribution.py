"""归因 / 贡献度拆解：把「总量变化」拆成各分组的贡献度并排序。

用途：让「定位」环节有量化依据——回答「哪个维度/环节贡献最大」，
而不是只给出「谁涨谁跌」。输入为 (analysis_month × 主维度) 的交叉汇总
（即 metric_engine 产出的 monthly_primary），输出可直接嵌入 extraction 的
attribution 字段，并交给全局洞察节点使用。
"""
from __future__ import annotations

import pandas as pd


def contribution_breakdown(
    frame: pd.DataFrame,
    dimension: str,
    metric: str,
    baseline: str,
    target: str,
    top_n: int | None = None,
) -> dict:
    """按 dimension 计算 metric 从 baseline 期到 target 期的贡献度拆解。

    - frame：含 analysis_month、dimension、metric 列的交叉汇总 DataFrame；
      同一 (month, dimension) 若有多行会先求和。
    - 贡献度 contribution_rate = 该分组 delta / 总量变化 total_change；
      总量变化为 0 时记为 null。
    - 结果按 |delta| 从大到小排序；top_n 非空时只保留前 N 个分组。
    """
    result = {
        "metric": metric,
        "group_dimension": dimension,
        "baseline": str(baseline),
        "target": str(target),
        "total_change": None,
        "items": [],
    }
    if frame is None or frame.empty or dimension not in frame.columns or metric not in frame.columns:
        return result

    scoped = frame.loc[frame["analysis_month"].astype(str).isin([str(baseline), str(target)])]
    if scoped.empty:
        return result

    pivoted = (
        scoped.groupby(["analysis_month", dimension], dropna=False)[metric]
        .sum()
        .unstack(level="analysis_month")
    )
    if str(baseline) not in pivoted.columns and str(target) not in pivoted.columns:
        return result

    base_series = pivoted.get(str(baseline), pd.Series(0.0, index=pivoted.index)).fillna(0.0)
    target_series = pivoted.get(str(target), pd.Series(0.0, index=pivoted.index)).fillna(0.0)
    delta = target_series - base_series
    total_change = float(delta.sum())

    items = []
    for group in pivoted.index:
        group_delta = float(delta.loc[group])
        items.append({
            "group": str(group),
            "baseline_value": round(float(base_series.loc[group]), 4),
            "target_value": round(float(target_series.loc[group]), 4),
            "delta": round(group_delta, 4),
            "contribution_rate": (
                round(group_delta / total_change, 6) if total_change else None
            ),
        })
    items.sort(key=lambda item: abs(item["delta"]), reverse=True)
    if top_n is not None:
        items = items[:top_n]

    result["total_change"] = round(total_change, 4)
    result["items"] = items
    return result


def attribution_for_metrics(
    frame: pd.DataFrame,
    dimension: str,
    metrics: list[str],
    baseline: str | None,
    target: str | None,
    top_n: int | None = None,
) -> list[dict]:
    """对多个指标分别产出贡献度拆解；缺少基准/报告期时返回空列表。"""
    if not baseline or not target or baseline == target:
        return []
    return [
        contribution_breakdown(frame, dimension, metric, baseline, target, top_n)
        for metric in metrics
    ]
