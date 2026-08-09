"""Boolean tag-expression parser and evaluator.

Grammar (case-insensitive keywords, `not` binds tightest, then `and`, then `or`):

    expr    := or_expr
    or_expr := and_expr ( OR and_expr )*
    and_expr:= not_expr ( AND not_expr )*
    not_expr:= NOT not_expr | atom
    atom    := TAG | '(' expr ')'

Tag names may be bare words (no whitespace/parens) or quoted with single or
double quotes to include spaces, e.g. `"road trip" and not private`.

Deliberately framework- and database-agnostic: evaluate() takes an
injectable resolver so this module has no sqlite3/FastAPI dependency and can
be unit tested with fake tag->file-id mappings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


class SearchSyntaxError(ValueError):
    pass


class Expr:
    __slots__ = ()


@dataclass(frozen=True, slots=True)
class TagTerm(Expr):
    name: str


@dataclass(frozen=True, slots=True)
class And(Expr):
    left: Expr
    right: Expr


@dataclass(frozen=True, slots=True)
class Or(Expr):
    left: Expr
    right: Expr


@dataclass(frozen=True, slots=True)
class Not(Expr):
    operand: Expr


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str  # "LPAREN" | "RPAREN" | "AND" | "OR" | "NOT" | "TAG"
    value: str


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == "(":
            tokens.append(_Token("LPAREN", "("))
            i += 1
            continue
        if c == ")":
            tokens.append(_Token("RPAREN", ")"))
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            j = i + 1
            while j < n and text[j] != quote:
                j += 1
            if j >= n:
                raise SearchSyntaxError(f"Unterminated quoted tag name at position {i}")
            tokens.append(_Token("TAG", text[i + 1 : j]))
            i = j + 1
            continue

        j = i
        while j < n and not text[j].isspace() and text[j] not in "()":
            j += 1
        word = text[i:j]
        i = j
        lowered = word.lower()
        if lowered == "and":
            tokens.append(_Token("AND", word))
        elif lowered == "or":
            tokens.append(_Token("OR", word))
        elif lowered == "not":
            tokens.append(_Token("NOT", word))
        else:
            tokens.append(_Token("TAG", word))
    return tokens


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> _Token | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _advance(self) -> _Token:
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def parse(self) -> Expr:
        expr = self._parse_or()
        trailing = self._peek()
        if trailing is not None:
            raise SearchSyntaxError(f"Unexpected token: {trailing.value!r}")
        return expr

    def _parse_or(self) -> Expr:
        left = self._parse_and()
        while (tok := self._peek()) is not None and tok.kind == "OR":
            self._advance()
            left = Or(left, self._parse_and())
        return left

    def _parse_and(self) -> Expr:
        left = self._parse_not()
        while (tok := self._peek()) is not None and tok.kind == "AND":
            self._advance()
            left = And(left, self._parse_not())
        return left

    def _parse_not(self) -> Expr:
        tok = self._peek()
        if tok is not None and tok.kind == "NOT":
            self._advance()
            return Not(self._parse_not())
        return self._parse_atom()

    def _parse_atom(self) -> Expr:
        tok = self._peek()
        if tok is None:
            raise SearchSyntaxError("Unexpected end of expression")
        if tok.kind == "TAG":
            self._advance()
            return TagTerm(tok.value)
        if tok.kind == "LPAREN":
            self._advance()
            expr = self._parse_or()
            closing = self._peek()
            if closing is None or closing.kind != "RPAREN":
                raise SearchSyntaxError("Expected closing parenthesis")
            self._advance()
            return expr
        raise SearchSyntaxError(f"Unexpected token: {tok.value!r}")


def parse(text: str) -> Expr:
    tokens = _tokenize(text)
    if not tokens:
        raise SearchSyntaxError("Empty search expression")
    return _Parser(tokens).parse()


def evaluate(
    expr: Expr,
    resolve_tag: Callable[[str], set[int]],
    universe: set[int],
) -> set[int]:
    """Evaluate expr to a set of matching file ids.

    resolve_tag(name) should return the set of file ids matching that tag
    name (already including supertag expansion). universe is the full set of
    candidate file ids, used as the base for `not`.
    """
    if isinstance(expr, TagTerm):
        return resolve_tag(expr.name)
    if isinstance(expr, And):
        left = evaluate(expr.left, resolve_tag, universe)
        right = evaluate(expr.right, resolve_tag, universe)
        return left & right
    if isinstance(expr, Or):
        left = evaluate(expr.left, resolve_tag, universe)
        right = evaluate(expr.right, resolve_tag, universe)
        return left | right
    if isinstance(expr, Not):
        return universe - evaluate(expr.operand, resolve_tag, universe)
    raise AssertionError(f"Unknown expression node: {expr!r}")
