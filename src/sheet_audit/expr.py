"""A tiny, safe language for arithmetic rules such as ``total = subtotal + tax``.

The LLM proposes calculation rules as text. That text is parsed here into a small tree of
column references, numbers, and + - * / operators. Nothing is ever passed to ``eval``,
so a malicious expression can at worst fail to parse.

Grammar:
    expr   := term (("+" | "-") term)*
    term   := factor (("*" | "/") factor)*
    factor := "-" factor | NUMBER | NAME | "[" quoted name "]" | "(" expr ")"
"""

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

MAX_EXPRESSION_CHARS = 200
MAX_NODES = 25

_TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?)"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"|\[(?P<qname>[^\[\]]{1,128})\]"
    r"|(?P<op>[-+*/()]))"
)


@dataclass(frozen=True)
class Num:
    value: float


@dataclass(frozen=True)
class Col:
    name: str


@dataclass(frozen=True)
class Neg:
    operand: "Node"


@dataclass(frozen=True)
class BinOp:
    op: str
    left: "Node"
    right: "Node"


Node = Num | Col | Neg | BinOp


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens, pos = [], 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"unexpected character at position {pos}")
        kind = m.lastgroup
        value = m.group(kind)
        tokens.append(("name" if kind == "qname" else kind, value))
        pos = m.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.pos = 0
        self.nodes = 0

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _take(self) -> tuple[str, str]:
        tok = self._peek()
        if tok is None:
            raise ValueError("unexpected end of expression")
        self.pos += 1
        return tok

    def _count(self, node: Node) -> Node:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise ValueError(f"expression is too complex (max {MAX_NODES} nodes)")
        return node

    def parse(self) -> Node:
        node = self._expr()
        if self._peek() is not None:
            raise ValueError(f"unexpected token {self._peek()[1]!r}")
        return node

    def _expr(self) -> Node:
        node = self._term()
        while (tok := self._peek()) and tok in (("op", "+"), ("op", "-")):
            self._take()
            node = self._count(BinOp(tok[1], node, self._term()))
        return node

    def _term(self) -> Node:
        node = self._factor()
        while (tok := self._peek()) and tok in (("op", "*"), ("op", "/")):
            self._take()
            node = self._count(BinOp(tok[1], node, self._factor()))
        return node

    def _factor(self) -> Node:
        kind, value = self._take()
        if (kind, value) == ("op", "-"):
            return self._count(Neg(self._factor()))
        if kind == "num":
            return self._count(Num(float(value)))
        if kind == "name":
            return self._count(Col(value))
        if (kind, value) == ("op", "("):
            node = self._expr()
            if self._take() != ("op", ")"):
                raise ValueError("missing closing parenthesis")
            return node
        raise ValueError(f"unexpected token {value!r}")


def parse(text: str) -> Node:
    """Parse an expression, raising ``ValueError`` on anything outside the grammar."""
    if not text or not text.strip():
        raise ValueError("expression is empty")
    if len(text) > MAX_EXPRESSION_CHARS:
        raise ValueError(f"expression is longer than {MAX_EXPRESSION_CHARS} characters")
    return _Parser(_tokenize(text)).parse()


def columns(node: Node) -> set[str]:
    """Return the column names an expression refers to."""
    if isinstance(node, Col):
        return {node.name}
    if isinstance(node, Neg):
        return columns(node.operand)
    if isinstance(node, BinOp):
        return columns(node.left) | columns(node.right)
    return set()


def evaluate(node: Node, values: dict[str, pd.Series]) -> pd.Series:
    """Evaluate row-wise over numeric Series. Division by zero yields NaN, not an error."""
    if isinstance(node, Num):
        index = next(iter(values.values())).index
        return pd.Series(node.value, index=index, dtype=float)
    if isinstance(node, Col):
        return values[node.name].astype(float)
    if isinstance(node, Neg):
        return -evaluate(node.operand, values)
    left, right = evaluate(node.left, values), evaluate(node.right, values)
    if node.op == "+":
        return left + right
    if node.op == "-":
        return left - right
    if node.op == "*":
        return left * right
    with np.errstate(divide="ignore", invalid="ignore"):
        return (left / right).replace([np.inf, -np.inf], np.nan)
