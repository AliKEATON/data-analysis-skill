#!/usr/bin/env python3
"""统一执行入口：按任务配置运行已支持的订单分析案例。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASK = ROOT / "my_case" / "task.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "order_analysis_2026-06_07"


def main() -> int:
    parser = argparse.ArgumentParser(description="按任务配置运行数据分析案例")
    parser.add_argument("--task", type=Path, default=DEFAULT_TASK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--hooks", type=str, default=None, help="本地 Agent 推理钩子的文件或模块路径（需提供 build_hooks()）")
    parser.add_argument("--validate", action="store_true", help="执行完成后运行端到端校验")
    args = parser.parse_args()

    task = args.task.resolve()
    output = args.output.resolve()
    preflight = ROOT / "scripts" / "validate_task_runtime.py"
    subprocess.run([sys.executable, str(preflight), str(task)], check=True)
    executor = ROOT / "scripts" / "execute_pipeline.py"
    executor_cmd = [sys.executable, str(executor), "--task", str(task), "--output", str(output)]
    if args.hooks:
        executor_cmd += ["--hooks", args.hooks]
    subprocess.run(executor_cmd, check=True)

    manifest = output / "run" / "manifest.json"
    if args.validate:
        validator = ROOT / "scripts" / "validate_run.py"
        subprocess.run([sys.executable, str(validator), str(manifest)], check=True)
    print(json.dumps({"task": str(task), "output": str(output), "manifest": str(manifest)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
