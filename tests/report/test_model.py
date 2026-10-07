import json

import pytest

from searchaudit import stats
from searchaudit.report.model import build_view_model


@pytest.fixture(scope="module")
def vm(fixture_results):
    return build_view_model(fixture_results)


def test_view_model_is_plain_json(vm):
    text = json.dumps(vm, allow_nan=False)
    assert json.loads(text)["n"] == 80


def test_fixture_shape(vm, fixture_results):
    assert vm["ids"] == ["exa", "parallel", "keen", "select", "routed"]
    assert len(vm["prompts"]) == 80 and len(vm["client"]["prompts"]) == 80
    assert {t["id"]: t["n"] for t in vm["types"]} == {
        "lookup": 22,
        "fresh": 14,
        "multihop": 18,
        "wide": 16,
        "domain": 10,
    }
    assert vm["counts"]["all"] == 80
    assert vm["counts"]["disagree"] == sum(r["disagree"] for r in vm["prompts"])
    assert (
        sum(
            vm["summary"][i]["k"] + vm["summary"][i]["partial"] + vm["summary"][i]["wrong"] for i in vm["ids"]
        )
        == 400
    )
    assert vm["meta"]["company"] == "StackAI" and vm["meta"]["notes"] == fixture_results.meta.notes


def test_headline_and_ties_follow_the_tests(vm):
    assert vm["leader"] == vm["by_accuracy"][0]
    assert vm["leader"] in vm["tied"]
    assert vm["headline"]["kind"] == "tie"
    assert vm["headline"]["text"].startswith(vm["labels"][vm["by_score"][0]])
    # summary scores are sorted the way by_score says
    scores = [vm["summary"][i]["score"] for i in vm["by_score"]]
    assert scores == sorted(scores, reverse=True)


def test_fixture_routes_cover_every_branch(vm):
    routes = {r["type"]: (r["choice"], r["status"]) for r in vm["guide"]["routes"]}
    assert routes["wide"] == ("select", "clear")
    assert routes["lookup"] == ("keen", "clear")
    assert ("keen", "lean") in routes.values()
    assert '"wide": "keenable-select"' in vm["guide"]["config"]
    assert vm["guide"]["to_select"] == ["list-building"]


def test_cost_at_volume_rows_and_estimates(vm):
    rows = {r["id"]: r for r in vm["guide"]["volumes"]}
    assert set(rows) == set(vm["ids"]) | {"router"}
    assert rows["exa"]["est"] is True and all(c["est"] for c in rows["exa"]["cells"])
    assert rows["keen"]["est"] is False
    assert rows["keen"]["cells"][1]["usd"] == pytest.approx(rows["keen"]["per_prompt"] * 1_000_000)


def test_routed_cells_carry_the_router_choice(vm):
    for r in vm["prompts"]:
        cell = r["cells"]["routed"]
        assert cell["via"] in ("keen", "select")
        assert cell["grade"] == r["cells"][cell["via"]]["grade"]  # derived, not an independent draw


def test_unique_sources_are_marked(vm):
    for r in vm["prompts"]:
        seen = {}
        for c in r["cells"].values():
            for d in {s["domain"] for s in c["sources"]}:
                seen[d] = seen.get(d, 0) + 1
        for c in r["cells"].values():
            assert all(s["unique"] == (seen[s["domain"]] == 1) for s in c["sources"])


def test_judge_section_is_honest_about_human_agreement(vm):
    assert vm["judge"]["human_agreement"] is None
    assert vm["judge"]["answers"] == 400 and vm["judge"]["missing"] == 0
    assert vm["judge"]["inconsistent"] == []


def test_h2h_pairs(vm):
    assert len(vm["h2h"]) == 20
    pair = vm["h2h"]["|".join(vm["h2h_default"])]
    assert pair["both"] + len(pair["only_a_idx"]) + len(pair["only_b_idx"]) + pair["neither"] == 80
    assert vm["h2h_default"][1] == "exa"  # uses_today is the default comparison


def test_sole_leader_and_tradeoff_on_small_audits(results_factory):
    vm = build_view_model(results_factory({"exa": "c", "keen": "cww", "select": "w"}))
    assert vm["headline"]["kind"] == "sole" and vm["leader"] == "exa" and vm["tied"] == ["exa"]
    # Exa correct everywhere but 20x the cost: with cost-heavy weights search wins value, Exa accuracy.
    vm = build_view_model(
        results_factory(
            {"exa": "c", "keen": "ccw", "select": "w"}, cost={"exa": 2.0, "keen": 0.01, "select": 0.01}
        ),
        stats.Weights(accuracy=10, cost=80, speed=10),
    )
    assert vm["by_score"][0] == "keen" and vm["headline"]["kind"] == "tradeoff"


def test_too_few_prompts(results_factory):
    vm = build_view_model(results_factory({"exa": "c", "keen": "c", "select": "c"}, n=12))
    assert vm["headline"]["kind"] == "insufficient"
    assert vm["client"]["headlineFixed"] is True


def test_missing_run_counts_as_wrong(results_factory):
    res = results_factory({"exa": "c", "keen": "c", "select": "c"})
    res.runs = [r for r in res.runs if not (r.prompt_id == 1 and r.provider == "keen")]
    vm = build_view_model(res)
    cell = vm["prompts"][0]["cells"]["keen"]
    assert cell["grade"] == "w" and cell["missing"] is True and cell["error"] == "missing run"
    res.judgments = [j for j in res.judgments if not (j.prompt_id == 2 and j.provider == "keen")]
    cell = build_view_model(res)["prompts"][1]["cells"]["keen"]
    assert cell["grade"] == "w" and cell["failure"] == "error"


def test_no_router_without_select(results_factory):
    res = results_factory({"exa": "c", "keen": "c", "select": "c"})
    res.providers = [p for p in res.providers if p.id != "select"]
    vm = build_view_model(res)
    assert vm["guide"]["available"] is False
    assert all(r["id"] != "router" for r in vm["guide"]["volumes"])


# ---------------------------------------------------------------- no-search baseline


def _with_baseline(grades: dict[str, str], base: str, n: int = 40, tiers: bool = False):
    from searchaudit.models import Judgment, ProviderInfo, Run

    from .conftest import CHECKS, make_results

    res = make_results(grades, n=n)
    info = ProviderInfo(id="none", label="No search (model memory)", short="No search", domain="", hex="#9ca3af")
    runs, judgments = list(res.runs), list(res.judgments)
    for i in range(1, n + 1):
        g = base[(i - 1) % len(base)]
        runs.append(Run(prompt_id=i, provider="none", answer="from memory", latency_ms=300, cost_usd=0.0001))
        judgments.append(Judgment(prompt_id=i, provider="none", grade=g, checks=CHECKS[g],
                                  failure_mode=None if g == "c" else "outdated"))
    prompts = [p.model_copy(update={"tier": "hard" if tiers and p.id % 2 else "routine" if tiers else None})
               for p in res.prompts]
    return res.model_copy(update={"providers": [*res.providers, info], "runs": runs,
                                  "judgments": judgments, "prompts": prompts})


def test_baseline_is_shown_but_never_recommended():
    # The baseline ties exa on accuracy and is far cheaper and faster: it must still not lead anything.
    res = _with_baseline({"exa": "c", "keen": "ccw", "select": "w"}, base="c")
    vm = build_view_model(res)
    assert "none" in vm["ids"] and "none" in vm["by_score"] and vm["baseline"] == "none"
    assert vm["by_score"][0] == "none"  # it is the best value on paper ...
    assert "No search" not in vm["headline"]["text"]  # ... but the headline names a search provider
    assert vm["leader"] != "none" and "none" not in vm["tied"]
    named = [i for f in vm["facts"] for i in f["ids"]]
    assert "none" not in named
    assert vm["h2h_default"][0] != "none"
    assert all(r["id"] != "none" for r in vm["guide"]["volumes"])
    assert all(t["best"] != "none" for t in vm["types"])
    assert vm["guide"]["fallback"] != "none"
    added = vm["facts"][-1]
    assert added["key"] == "Search added" and added["value"] == "Exa: +0 prompts over no search"
    assert "within chance" in added["detail"]
    assert vm["client"]["baseline"] == ["none"]


def test_search_lift_counts_prompts_baseline_missed():
    res = _with_baseline({"exa": "c", "keen": "ccw", "select": "w"}, base="w", tiers=True)
    vm = build_view_model(res)
    lift = {r["id"]: r for r in vm["lift"]["rows"]}
    assert lift["exa"]["gained"] == 40 and lift["keen"]["gained"] == 27 and lift["select"]["gained"] == 0
    assert lift["exa"]["tiers"] == {"routine": 20, "hard": 20}
    assert "significantly better" in vm["facts"][-1]["detail"]
    assert all("none" not in t["tied"] and t["value_winner"] != "none" for t in vm["tiers"])


def test_baseline_does_not_change_other_providers_scores():
    plain = build_view_model(_with_baseline({"exa": "c", "keen": "ccw", "select": "w"}, base="c"))
    from .conftest import make_results

    without = build_view_model(make_results({"exa": "c", "keen": "ccw", "select": "w"}))
    for pid in ("exa", "keen", "select"):
        assert plain["summary"][pid]["score"] == pytest.approx(without["summary"][pid]["score"])
    assert [i for i in plain["by_score"] if i != "none"] == without["by_score"]


def test_no_baseline_means_no_lift():
    from .conftest import make_results

    vm = build_view_model(make_results({"exa": "c", "keen": "ccw", "select": "w"}))
    assert vm["lift"] == {"available": False, "facts": []} and vm["baseline"] is None
    assert [f["key"] for f in vm["facts"]] == ["Most correct", "Cheapest of the most correct", "Fastest"]


def test_economics_break_even_against_uses_today() -> None:
    import json
    from pathlib import Path

    from searchaudit.models import AuditResults
    from searchaudit.report.model import build_view_model

    results = AuditResults(**json.loads(Path("audits/fixture/results.json").read_text()))
    e = build_view_model(results)["economics"]
    ref = e["ref"]
    assert e["rows"][ref]["request_ratio"] == 1.0
    for r in e["rows"].values():
        if r["price_ratio"] and r["request_ratio"]:
            assert r["cheaper"] == (r["request_ratio"] < r["price_ratio"])
