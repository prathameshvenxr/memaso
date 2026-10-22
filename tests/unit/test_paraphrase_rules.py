"""Updates phrased as events ("we raised the budget to 80k") are read by rules, with no model, and a refined
attribute name ("monthly budget" for "budget") is matched only when the values make that safe."""
from datetime import UTC, datetime

import pytest

from memory_core.ingest.rules import extract_rules, key_of, rewrite_change
from memory_core.ingest.truth import rules_judge
from memory_core.types import Kind, Memory, Scope, Verdict

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def mem(text: str, id: str = "m1") -> Memory:
    return Memory(id=id, scope=Scope("t", "u"), text=text, kind=Kind.fact, entities=[], confidence=0.8, importance=0.5,
                  valid_from=NOW, valid_to=None, supersedes=[], source_doc_ids=[], extractor_version="t", created_at=NOW)


@pytest.mark.parametrize("said,plain", [
    ("We've raised our monthly budget to 80k", "monthly budget is 80k"),
    ("We lowered the CPA target to 380", "the CPA target is 380"),
    ("We changed the CPA target from 420 to 380", "the CPA target is 380"),
    ("The budget has been updated to 90k", "The budget is 90k"),
    ("The budget went up to 90k", "The budget is 90k"),
    ("I've moved the daily cap to 5k", "the daily cap is 5k"),
    ("We switched the reports to weekly", "the reports is weekly"),
])
def test_an_update_phrased_as_an_event_becomes_a_plain_statement(said, plain):
    assert rewrite_change(said) == plain
    assert [c.text for c in extract_rules(said + ".")] == [plain]


@pytest.mark.parametrize("said", [
    "Should we raise the budget to 80k?",           # a question states nothing
    "Raise the budget to 80k",                      # an instruction, not something that happened
    "I'd like to raise the budget to 80k",          # an intention
    "We will raise the budget to 80k",              # the future
    "We raised the alarm to the team",              # no value
    "We might have raised the budget to 80k",       # hedged
])
def test_requests_intentions_and_hedges_are_not_updates(said):
    # an intention may be kept as a preference or a decision, or stay verbatim-only, but never becomes a fact
    assert not [c for c in extract_rules(said) if c.kind == Kind.fact]
    assert not [c for c in extract_rules(said) if c.text.lower().startswith(("the budget is", "budget is"))]


def test_a_leading_now_is_not_part_of_the_value():
    assert key_of("The budget is now 80k") == ("the budget", "80k")
    assert key_of("Our monthly budget is now set to 80k")[1] == "80k"


def test_a_speaker_name_that_opens_a_sentence_is_an_entity_when_the_text_names_it_again():
    cands = extract_rules("Priya is the account lead for Acme. Priya sent the deck.")
    assert "Priya" in cands[0].entities and "Acme" in cands[0].entities
    assert "Budget" not in extract_rules("Budget is 50k")[0].entities  # a common noun that only starts a sentence


def test_a_refined_attribute_with_a_comparable_value_updates_the_plain_one_and_the_reverse():
    d = rules_judge("monthly budget is 80k", [mem("budget is 50k")])
    assert d and d.verdict == Verdict.updates and d.ids == ["m1"]
    d = rules_judge("budget is 90k", [mem("monthly budget is 80k")])
    assert d and d.verdict == Verdict.updates


def test_a_refinement_whose_value_is_far_off_is_not_guessed():
    assert rules_judge("daily budget is 2k", [mem("budget is 50k")]) is None  # 25x apart: a different attribute


def test_other_extra_words_change_the_attribute_so_they_stay_ambiguous():
    assert rules_judge("CPA target is 400", [mem("CPA is 420")]) is None
    assert rules_judge("monthly budget is high", [mem("budget is low")]) is None  # no comparable numbers


def test_same_attribute_and_same_value_is_a_duplicate_not_a_new_version():
    d = rules_judge("The budget is 50k", [mem("budget is 50k")])
    assert d and d.verdict == Verdict.duplicate


def test_configured_aliases_name_the_same_attribute():
    aliases = [["cpa", "cost per acquisition"]]
    d = rules_judge("cost per acquisition is 400", [mem("CPA is 420")], aliases=aliases)
    assert d and d.verdict == Verdict.updates
    assert rules_judge("cost per acquisition is 400", [mem("CPA is 420")]).verdict == Verdict.new  # no alias: different


def test_two_different_periods_are_different_attributes():
    d = rules_judge("monthly budget is 80k", [mem("daily budget is 2k")])
    assert d and d.verdict == Verdict.new


# ---------------------------------------------------------------- found by independent review: no junk facts
@pytest.mark.parametrize("said", [
    "Please set the budget to 90k",                 # an instruction that happens to start with a capitalised word
    "Never cut the budget to 50k",                  # a prohibition
    "Always set the CPA target to 400",
    "Kindly set the daily cap to 5k",
    "We set the budget to 90k next week",           # the future
    "He dropped out at 18",
    "I bumped into him at 5",
    "She cut the cake at 5pm",
    "We went to 3 countries",                       # a pronoun is not an attribute
    "He got moved to 4 pm",
    "The kids went up to 3 floors",                 # "floors" is not a unit
    "We cut the budget in half to 40k",             # a phrase with a preposition is not an attribute
    "I upped it to 5",
])
def test_instructions_prohibitions_the_future_and_everyday_sentences_make_no_fact(said):
    assert extract_rules(said) == []


def test_trailing_time_words_are_not_part_of_the_value_and_a_subject_is_optional_for_a_past_verb():
    assert [c.text for c in extract_rules("We raised the budget to 80k this month")] == ["the budget is 80k"]
    assert [c.text for c in extract_rules("Changed the CPA target from 420 to 380")] == ["the CPA target is 380"]
    assert extract_rules("Change the CPA target to 380") == []   # the base form is an instruction


def test_extracting_a_very_long_document_stays_linear():
    import time

    doc = " ".join(f"Alpha is {i}." for i in range(8000))
    t = time.perf_counter()
    extract_rules(doc)
    assert time.perf_counter() - t < 2.0   # it took 38 s on a 200 KB document before the word counts were hoisted


def test_config_defaults_are_not_shared_between_instances():
    from memory_core.config import load_config

    a = load_config(None, {})
    a.truth["aliases"].append(["mrr", "monthly recurring revenue"])
    assert ["mrr", "monthly recurring revenue"] not in load_config(None, {}).truth["aliases"]
