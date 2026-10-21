"""jsonutil.extract_json 的解析行为。"""

from __future__ import annotations

import pytest

from manager.shared.jsonutil import extract_json


def test_plain_json_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_plain_json_array():
    assert extract_json("[1, 2, 3]") == [1, 2, 3]


def test_plain_json_scalar():
    assert extract_json("42") == 42


def test_json_fence():
    assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}


def test_json_fence_uppercase_language():
    assert extract_json('```JSON\n{"a": 2}\n```') == {"a": 2}


def test_fence_without_language():
    assert extract_json('```\n{"a": 2}\n```') == {"a": 2}


def test_json_embedded_in_explanation():
    assert extract_json('好的，结果如下：{"a": 3}\n希望有帮助。') == {"a": 3}


def test_array_embedded_in_explanation():
    assert extract_json("结果是 [1, 2, 3] 这样。") == [1, 2, 3]


def test_nested_braces():
    text = '前言 {"a": {"b": [1, {"c": 2}]}} 后语'
    assert extract_json(text) == {"a": {"b": [1, {"c": 2}]}}


def test_braces_inside_string_are_ignored():
    text = '{"a": "}", "b": "{"}'
    assert extract_json(text) == {"a": "}", "b": "{"}


def test_escaped_quote_inside_string():
    text = '{"a": "he said \\"hi\\""}'
    assert extract_json(text) == {"a": 'he said "hi"'}


def test_first_balanced_candidate_that_fails_parse_is_skipped():
    text = '注意 {这不是 JSON} 然后 {"a": 4}'
    assert extract_json(text) == {"a": 4}


def test_fence_wins_over_bare_scan():
    text = '例子：{"old": 0}\n```json\n{"new": 1}\n```'
    assert extract_json(text) == {"new": 1}


@pytest.mark.parametrize("text", ["", "   ", "\n\n", None])
def test_empty_output_raises(text):
    with pytest.raises(ValueError):
        extract_json(text)


def test_output_without_json_raises():
    with pytest.raises(ValueError):
        extract_json("完全没有 JSON 的一段话")


def test_unbalanced_braces_raise():
    with pytest.raises(ValueError):
        extract_json('{"a": 1')
