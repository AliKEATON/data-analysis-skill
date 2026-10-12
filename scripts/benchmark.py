#!/usr/bin/env python3
"""性能基准：测量通用流水线在订单案例上的端到端耗时与 Python 峰值内存。

说明：
- 耗时用 time.perf_counter 精确测量。
- 内存用 tracemalloc 测量（仅覆盖 Python 层分配，不含 pandas/numpy 的 C 扩展内存，
  实际进程内存会更高；如需精确 RSS 可安装 psutil）。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.execute_pipeline import run  # noqa: E402

DEFAULT_TASK = ROOT / "my_case" / "task.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "order_analysis_2026-06_07"


def measure_once(task_path: Path, output: Path) -> dict:
    """执行一次流水线，返回耗时与峰值内存。"""
    tracemalloc.start()
    start = time.perf_counter()
    snapshot = run(task_path, output)
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "elapsed_seconds": round(elapsed, 3),
        "peak_python_memory_bytes": peak,
        "source_rows": snapshot.get("source_rows"),
        "scope_rows": snapshot.get("scope_rows"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="测量流水线性能基准")
    parser.add_argument("--task", type=Path, default=DEFAULT_TASK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runs", type=int, default=2, help="运行次数（取中位数）")
    args = parser.parse_args()

    results = [measure_once(args.task, args.output) for _ in range(args.runs)]
    elapsed = [r["elapsed_seconds"] for r in results]
    report = {
        "task": str(args.task),
        "runs": args.runs,
        "elapsed_seconds": {
            "min": min(elapsed),
            "median": statistics.median(elapsed),
            "max": max(elapsed),
        },
        "peak_python_memory_bytes": max(r["peak_python_memory_bytes"] for r in results),
        "source_rows": results[0].get("source_rows"),
        "scope_rows": results[0].get("scope_rows"),
        "note": "内存为 tracemalloc 的 Python 层分配，不含 pandas/numpy C 扩展内存",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
