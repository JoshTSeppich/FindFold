"""
The scoring band decides which leads cost a model call. These tests pin that
down without a network: ANTHROPIC_API_KEY is set to a dummy, the anthropic
client is a stub, and score_batch is replaced by a recorder.
"""

import asyncio
import sys
import types

import pytest

from config import CLAUDE_AMBIGUOUS_MAX, CLAUDE_AMBIGUOUS_MIN, CLAUDE_FINAL_THRESHOLD
from lead_pipeline.filter import claude_scorer
from lead_pipeline.filter.claude_scorer import band_decision, rescore_ambiguous

BELOW = round(CLAUDE_AMBIGUOUS_MIN - 0.05, 3)
INSIDE = round((CLAUDE_AMBIGUOUS_MIN + CLAUDE_AMBIGUOUS_MAX) / 2, 3)
ABOVE = round(CLAUDE_AMBIGUOUS_MAX + 0.05, 3)


def lead(name: str, score: float) -> dict:
    return {"company_name": name, "domain": f"{name}.example", "icp_score": score}


@pytest.fixture
def sent_to_model(monkeypatch):
    """Turn Claude on with no network. Returns the list of company names that reached score_batch."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-never-used")
    stub = types.ModuleType("anthropic")
    stub.AsyncAnthropic = lambda api_key: object()
    monkeypatch.setitem(sys.modules, "anthropic", stub)

    sent: list[str] = []

    async def record_batch(client, batch, sem):
        for item in batch:
            sent.append(item["company_name"])
            item["icp_score"] = item["claude_score"] = round(CLAUDE_FINAL_THRESHOLD + 0.1, 3)

    monkeypatch.setattr(claude_scorer, "score_batch", record_batch)
    return sent


def test_band_decision_is_inclusive_at_both_ends():
    assert band_decision(BELOW) == "drop"
    assert band_decision(CLAUDE_AMBIGUOUS_MIN) == "ask"
    assert band_decision(INSIDE) == "ask"
    assert band_decision(CLAUDE_AMBIGUOUS_MAX) == "ask"
    assert band_decision(ABOVE) == "accept"


def test_below_the_band_is_never_sent_to_the_model(sent_to_model):
    result = asyncio.run(rescore_ambiguous([lead("cold", BELOW)]))
    assert sent_to_model == []
    assert result == []


def test_inside_the_band_is_sent_to_the_model(sent_to_model):
    result = asyncio.run(rescore_ambiguous([lead("maybe", INSIDE)]))
    assert sent_to_model == ["maybe"]
    assert [item["company_name"] for item in result] == ["maybe"]
    assert result[0]["claude_score"] is not None


def test_above_the_band_is_accepted_without_a_model_call(sent_to_model):
    hot = lead("hot", ABOVE)
    result = asyncio.run(rescore_ambiguous([hot]))
    assert sent_to_model == []
    assert result == [hot]
    assert "claude_score" not in hot


def test_mixed_batch_partitions_by_band(sent_to_model):
    leads = [lead("cold", BELOW), lead("maybe", INSIDE), lead("hot", ABOVE)]
    result = asyncio.run(rescore_ambiguous(leads))
    assert sent_to_model == ["maybe"]
    assert sorted(item["company_name"] for item in result) == ["hot", "maybe"]
