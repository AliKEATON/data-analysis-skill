#!/usr/bin/env python3
"""指标公式的 AST 白名单校验与求值。"""
from __future__ import annotations

import ast
import operator
from typing import Mapping

import numpy as np
import pandas as pd

_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class FormulaError(ValueError):
    pass


def parse_formula(formula: str, allowed_names: set[str]) -> ast.Expression:
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"公式语法错误：{exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id not in allowed_names:
                raise FormulaError(f"公式引用未知指标：{node.id}")
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise FormulaError("公式只允许数值常量")
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in {*_BINARY, ast.Div}:
                raise FormulaError("公式只允许 + - * / 运算")
        elif isinstance(node, ast.UnaryOp):
            if type(node.op) not in _UNARY:
                raise FormulaError("公式只允许一元正负号")
        elif not isinstance(node, (ast.Expression, ast.Load, ast.operator, ast.unaryop)):
            raise FormulaError(f"公式不允许 {type(node).__name__}")
    return tree


def evaluate_formula(formula: str, values: Mapping[str, object], zero_denominator: str):
    tree = parse_formula(formula, set(values))

    def evaluate(node):
        if isinstance(node, ast.Expression): return evaluate(node.body)
        if isinstance(node, ast.Name): return values[node.id]
        if isinstance(node, ast.Constant): return node.value
        if isinstance(node, ast.UnaryOp): return _UNARY[type(node.op)](evaluate(node.operand))
        if isinstance(node, ast.BinOp):
            left, right = evaluate(node.left), evaluate(node.right)
            if not isinstance(node.op, ast.Div): return _BINARY[type(node.op)](left, right)
            zero = right == 0
            has_zero = bool(zero.any()) if hasattr(zero, "any") else bool(zero)
            if has_zero and zero_denominator == "error": raise FormulaError("公式分母为零")
            with np.errstate(divide="ignore", invalid="ignore"):
                result = left / right
            if has_zero:
                replacement = np.nan if zero_denominator == "null" else 0
                if isinstance(result, pd.Series): result = result.mask(zero, replacement)
                elif zero: result = replacement
            return result
        raise FormulaError(f"公式不允许 {type(node).__name__}")

    return evaluate(tree)
