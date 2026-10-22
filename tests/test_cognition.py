"""cognition/cognition.py 的帧执行与修复逻辑。"""

from __future__ import annotations

from manager.cognition.cognition import Cognition, FrameResult
from manager.shared.types import DecisionError, LLMError


class _Cfg:
    frame_timeout_seconds = 5.0
    max_decision_repair = 1
    next_wake_max_seconds = 3600


class _FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, timeout=None):
        self.calls.append((messages, timeout))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _FakeParser:
    def __init__(self, results):
        self.results = list(results)
        self.seen = []

    def parse(self, raw, inbox_ids=None):
        self.seen.append((raw, inbox_ids))
        item = self.results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


_SIT = {"now": "2026-10-21T12:54:48+08:00"}
_CAPS = []


def _make(llm, parser, cfg=None):
    return Cognition(cfg or _Cfg(), llm, parser=parser)


def test_first_try_ok():
    llm = _FakeLLM(["raw-1"])
    parser = _FakeParser(["decision-1"])
    result = _make(llm, parser).run(_SIT, _CAPS)
    assert isinstance(result, FrameResult)
    assert result.ok
    assert result.decision == "decision-1"
    assert result.raw == "raw-1"
    assert result.repaired is False
    assert result.error is None
    assert parser.seen == [("raw-1", None)]
    assert llm.calls[0][1] == 5.0


def test_passes_inbox_ids():
    llm = _FakeLLM(["raw"])
    parser = _FakeParser(["decision"])
    _make(llm, parser).run(_SIT, _CAPS, inbox_ids={"ev_1", "ev_2"})
    assert parser.seen == [("raw", {"ev_1", "ev_2"})]


def test_llm_failure_no_repair():
    llm = _FakeLLM([LLMError("boom")])
    parser = _FakeParser([])
    result = _make(llm, parser).run(_SIT, _CAPS)
    assert not result.ok
    assert result.decision is None
    assert "LLM 调用失败" in result.error
    assert result.repaired is False
    assert parser.seen == []
    assert len(llm.calls) == 1


def test_repair_success():
    llm = _FakeLLM(["bad", "good"])
    parser = _FakeParser([DecisionError("bad json"), "decision-ok"])
    result = _make(llm, parser).run(_SIT, _CAPS)
    assert result.ok
    assert result.decision == "decision-ok"
    assert result.raw == "good"
    assert result.repaired is True
    assert result.error is None
    assert len(llm.calls) == 2
    assert parser.seen[0][0] == "bad"
    assert parser.seen[1][0] == "good"


def test_repair_prompt_has_previous_raw():
    llm = _FakeLLM(["bad", "good"])
    parser = _FakeParser([DecisionError("bad json"), "decision-ok"])
    _make(llm, parser).run(_SIT, _CAPS)
    msgs = llm.calls[1][0]
    assert msgs[-2] == {"role": "assistant", "content": "bad"}
    assert msgs[-1]["role"] == "user"
    assert "bad json" in msgs[-1]["content"]


def test_repair_call_failure():
    llm = _FakeLLM(["bad", LLMError("down")])
    parser = _FakeParser([DecisionError("bad json")])
    result = _make(llm, parser).run(_SIT, _CAPS)
    assert not result.ok
    assert result.decision is None
    assert result.raw == "bad"
    assert result.repaired is True
    assert "修复调用失败" in result.error


def test_repair_still_invalid():
    llm = _FakeLLM(["bad", "still-bad"])
    parser = _FakeParser([DecisionError("e1"), DecisionError("e2")])
    result = _make(llm, parser).run(_SIT, _CAPS)
    assert not result.ok
    assert result.decision is None
    assert result.raw == "still-bad"
    assert result.repaired is True
    assert "Decision 仍不合法" in result.error
    assert "e2" in result.error


def test_repair_disabled():
    cfg = _Cfg()
    cfg.max_decision_repair = 0
    llm = _FakeLLM(["bad"])
    parser = _FakeParser([DecisionError("bad json")])
    result = _make(llm, parser, cfg).run(_SIT, _CAPS)
    assert not result.ok
    assert result.repaired is False
    assert "bad json" in result.error
    assert len(llm.calls) == 1
