from collections.abc import Callable

import pytest

from tagger import search


def _resolver(mapping: dict[str, set[int]]) -> Callable[[str], set[int]]:
    def resolve(name: str) -> set[int]:
        return mapping.get(name, set())

    return resolve


def test_and_or_precedence_and_binds_tighter_than_or() -> None:
    expr = search.parse("a and b or c")
    resolver = _resolver({"a": {1, 2}, "b": {2, 3}, "c": {4}})
    result = search.evaluate(expr, resolver, universe={1, 2, 3, 4})
    # (a and b) or c
    assert result == {2, 4}


def test_not_binds_tighter_than_and() -> None:
    expr = search.parse("a and not b")
    resolver = _resolver({"a": {1, 2, 3}, "b": {2}})
    result = search.evaluate(expr, resolver, universe={1, 2, 3})
    assert result == {1, 3}


def test_parens_override_precedence() -> None:
    expr = search.parse("a and (b or c)")
    resolver = _resolver({"a": {1, 2}, "b": {2}, "c": {3}})
    result = search.evaluate(expr, resolver, universe={1, 2, 3})
    assert result == {2}


def test_case_insensitive_operators() -> None:
    expr = search.parse("a AND NOT b")
    resolver = _resolver({"a": {1, 2}, "b": {2}})
    result = search.evaluate(expr, resolver, universe={1, 2})
    assert result == {1}


def test_quoted_tag_names_with_spaces() -> None:
    expr = search.parse('"road trip" and not private')
    assert isinstance(expr, search.And)
    assert isinstance(expr.left, search.TagTerm)
    assert expr.left.name == "road trip"


def test_unbalanced_parens_raise() -> None:
    with pytest.raises(search.SearchSyntaxError):
        search.parse("(a and b")


def test_empty_expression_raises() -> None:
    with pytest.raises(search.SearchSyntaxError):
        search.parse("   ")


def test_missing_operator_between_tags_raises() -> None:
    with pytest.raises(search.SearchSyntaxError):
        search.parse("a b")


def test_unknown_tag_resolves_to_empty_set() -> None:
    expr = search.parse("nonexistent")
    resolver = _resolver({})
    result = search.evaluate(expr, resolver, universe={1, 2, 3})
    assert result == set()
