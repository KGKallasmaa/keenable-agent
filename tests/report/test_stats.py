import math

import pytest

from searchaudit import stats
from searchaudit.stats import Outcome, Weights

# ---------------------------------------------------------------- known values


def test_wilson_known_values():
    lo, hi = stats.wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-4)
    assert hi == pytest.approx(0.9433, abs=1e-4)
    assert stats.wilson(0, 0) == (0.0, 0.0)
    lo, hi = stats.wilson(10, 10)
    assert hi == 1.0 and lo == pytest.approx(0.7225, abs=1e-4)


def test_mcnemar_exact_known_values():
    # b=1, c=9: 2 * (C(10,0) + C(10,1)) / 2^10
    assert stats.mcnemar_exact(1, 9) == pytest.approx(22 / 1024)
    assert stats.mcnemar_exact(9, 1) == stats.mcnemar_exact(1, 9)
    assert stats.mcnemar_exact(0, 6) == pytest.approx(2 / 64)
    assert stats.mcnemar_exact(0, 0) == 1.0
    assert stats.mcnemar_exact(5, 5) == 1.0  # capped at 1


def test_holm_adjustment_keeps_input_order_and_monotone():
    adj = stats.holm([0.01, 0.04, 0.03])
    assert adj == pytest.approx([0.03, 0.06, 0.06])
    assert stats.holm([0.5, 0.9]) == pytest.approx([1.0, 1.0])
    assert stats.holm([]) == []


def test_paired_bootstrap_is_seeded_and_brackets_the_difference():
    a = [True] * 30 + [False] * 10
    b = [True] * 20 + [False] * 20
    ci = stats.paired_bootstrap_diff(a, b, seed=3)
    assert ci == stats.paired_bootstrap_diff(a, b, seed=3)
    assert ci.diff == pytest.approx(0.25)
    assert ci.low <= ci.diff <= ci.high
    assert ci.low > 0
    same = stats.paired_bootstrap_diff(a, a)
    assert (same.diff, same.low, same.high) == (0, 0, 0)


def test_median_and_percentile():
    assert stats.median([3, 1, 2, 10]) == 2.5
    assert stats.median([]) == 0.0
    assert stats.percentile(list(range(11)), 0.9) == 9


# ---------------------------------------------------------------- value score


def test_fast_wrong_answer_does_not_penalize_correct_ones():
    """Eng review R1 regression: speed and cost bars come from non-wrong answers only."""
    w = Weights(accuracy=60, cost=25, speed=15)
    with_fast_wrong = {
        "a": Outcome("c", cost=0.02, seconds=10),
        "b": Outcome("c", cost=0.04, seconds=20),
        "x": Outcome("w", cost=0.001, seconds=0.5),  # fast, cheap and wrong
    }
    without = {k: v for k, v in with_fast_wrong.items() if k != "x"}
    s1, s2 = stats.prompt_scores(with_fast_wrong, w), stats.prompt_scores(without, w)
    assert s1["x"] == 0
    assert s1["a"] == pytest.approx(s2["a"]) == pytest.approx(1.0)
    assert s1["b"] == pytest.approx(s2["b"]) == pytest.approx((60 + 25 * 0.5 + 15 * 0.5) / 100)


def test_partial_credit_and_all_wrong():
    scores = stats.prompt_scores({"a": Outcome("p", 0.01, 1), "b": Outcome("c", 0.01, 1)})
    assert scores == {"a": pytest.approx(0.5), "b": pytest.approx(1.0)}
    assert stats.prompt_scores({"a": Outcome("w", 1, 1), "b": Outcome("w", 2, 2)}) == {"a": 0, "b": 0}


def test_zero_weights_and_zero_cost_do_not_divide_by_zero():
    s = stats.prompt_scores({"a": Outcome("c", 0.0, 0.0), "b": Outcome("c", 0.01, 2)}, Weights(0, 0, 0))
    assert all(math.isfinite(v) for v in s.values())
    s = stats.prompt_scores({"a": Outcome("c", 0.0, 1), "b": Outcome("c", 0.01, 2)})
    assert s["a"] == pytest.approx(1.0) and s["b"] < s["a"]


def test_value_scores_average_on_0_100():
    prompts = [
        {"a": Outcome("c", 1, 1), "b": Outcome("w", 1, 1)},
        {"a": Outcome("w", 1, 1), "b": Outcome("w", 1, 1)},
    ]
    assert stats.value_scores(prompts) == {"a": pytest.approx(50.0), "b": 0.0}
    assert stats.value_scores([]) == {}


# ---------------------------------------------------------------- pareto and ties


def test_pareto_front_dominance():
    points = {
        "cheap_good": (0.8, 0.02),
        "pricey_best": (0.9, 0.09),
        "dominated": (0.7, 0.05),
        "same": (0.8, 0.02),
    }
    front = stats.pareto_front(points)
    assert front == {"cheap_good", "pricey_best", "same"}  # equal points do not dominate each other
    assert stats.pareto_front({"only": (0.1, 1.0)}) == {"only"}


def test_accuracy_ties_with_holm():
    n = 60
    leader = [True] * 50 + [False] * 10
    close = [True] * 48 + [False] * 12  # 2 discordant prompts: tie
    far = [True] * 20 + [False] * 40  # 30 discordant all for the leader: not tied
    ties = stats.accuracy_ties({"l": leader, "c": close, "f": far}, ["l", "c", "f"])
    assert ties.leader == "l"
    assert ties.tied == frozenset({"l", "c"})
    assert ties.adjusted_p["f"] < 0.05 <= ties.adjusted_p["c"]
    assert len(leader) == n


# ---------------------------------------------------------------- routes (R3)


def test_route_clear_select_wins():
    search = [False] * 8 + [True] * 4
    select = [True] * 12
    d = stats.route_for_type(search, select)
    assert (d.choice, d.status, d.search_only, d.select_only) == ("select", "clear", 0, 8)
    assert d.p < 0.05


def test_route_clear_search_wins():
    d = stats.route_for_type([True] * 12, [False] * 7 + [True] * 5)
    assert (d.choice, d.status) == ("keen", "clear")


def test_route_lean_defaults_to_search_even_when_select_is_ahead():
    search = [False] * 3 + [True] * 9
    select = [True] * 12
    d = stats.route_for_type(search, select)
    assert (d.choice, d.status) == ("keen", "lean")
    assert d.p >= 0.05 and d.select_only == 3


def test_route_too_few_prompts():
    d = stats.route_for_type([False] * 9, [True] * 9)
    assert (d.choice, d.status) == ("keen", "too_few")


# ---------------------------------------------------------------- headline (R2)

LABELS = {"a": "Alpha", "b": "Beta", "c": "Gamma"}


def _ties(leader, tied):
    return stats.Ties(leader, frozenset(tied), {})


def test_headline_tie():
    h = stats.headline(
        "Acme", LABELS, 80, {"a": 60, "b": 58, "c": 40}, ["b", "a", "c"], _ties("a", {"a", "b"})
    )
    assert h.kind == "tie"
    assert h.text.startswith("Beta gives Acme the best balance") and "2 providers tie" in h.text


def test_headline_sole_leader():
    h = stats.headline("Acme", LABELS, 80, {"a": 70, "b": 50, "c": 40}, ["a", "b", "c"], _ties("a", {"a"}))
    assert h.kind == "sole"
    assert (
        h.text
        == "Alpha is the most accurate provider for Acme (70 of 80 correct) and the best value overall."
    )


def test_headline_value_winner_less_accurate_names_the_tradeoff():
    h = stats.headline("Acme", LABELS, 80, {"a": 70, "b": 50, "c": 40}, ["b", "a", "c"], _ties("a", {"a"}))
    assert h.kind == "tradeoff"
    assert "Beta is the best value" in h.text and "Alpha answers significantly more" in h.text
    assert "(70 vs 50 of 80)" in h.text


def test_headline_insufficient_below_30_prompts():
    h = stats.headline("Acme", LABELS, 29, {"a": 20, "b": 10, "c": 5}, ["a", "b", "c"], _ties("a", {"a"}))
    assert h.kind == "insufficient" and h.text.startswith("Not enough evidence yet: 29 prompts")


def test_headline_insufficient_when_a_provider_mostly_failed():
    h = stats.headline(
        "Acme",
        LABELS,
        80,
        {"a": 60, "b": 58, "c": 10},
        ["a", "b", "c"],
        _ties("a", {"a", "b"}),
        failed={"c": 41},
    )
    assert h.kind == "insufficient" and "Gamma failed on 41 of 80" in h.text


# ---------------------------------------------------------------- cost at volume


def test_cost_at_volume_propagates_estimates():
    rows = stats.cost_at_volume(0.02, estimated=True)
    assert [r.volume for r in rows] == [100_000, 1_000_000, 10_000_000]
    assert rows[1].usd == pytest.approx(20_000) and all(r.estimated for r in rows)
    assert not any(r.estimated for r in stats.cost_at_volume(0.02, estimated=False))
