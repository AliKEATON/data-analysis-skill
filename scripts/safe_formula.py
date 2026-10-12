#!/usr/bin/env python3
"""向后兼容入口；安全公式实现位于 runtime.safe_formula。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.safe_formula import FormulaError, evaluate_formula, parse_formula  # noqa: E402,F401
