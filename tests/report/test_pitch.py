"""The SpaceX walkthrough stays empty until every required cell is judged."""

from pathlib import Path

from searchaudit.config import load_config, load_prompts
from searchaudit.cost import grok_pin_error, preflight_usd
from searchaudit.models import (
    AuditMeta,
    AuditResults,
    Citation,
    Judgment,
    Prompt,
    ProviderInfo,
    Run,
    ToolCall,
)
from searchaudit.report.model import build_view_model
from searchaudit.report.pitch import _chart, _hypothesis, _tick, build_pitch


def test_grok_pin_stops_unless_the_id_is_the_only_match():
    assert grok_pin_error(["x-ai/grok-4.7"], "x-ai/grok-4.7") is None
    assert grok_pin_error([], "x-ai/grok-4.7")
    assert grok_pin_error(["x-ai/grok-4.7", "x-ai/grok-4.7-2026"], "x-ai/grok-4.7")
    assert grok_pin_error(["deepseek/deepseek-v4.1-flash"], "openrouter/auto") is None


def test_preflight_uses_the_cap_as_a_gate():
    estimate = preflight_usd(60, ["exa", "parallel", "keen", "select", "grok", "routed"], {"keenable": 0.004})
    assert estimate < 200
    assert preflight_usd(60, ["exa"], {"keenable": 0.004}) > 1


def test_spacex_run_configs_stay_capped():
    jobs = load_config("audits/spacex-jobs/audit.yaml")
    assert jobs.model == "x-ai/grok-4.7"
    assert jobs.cost_cap_usd == 800  # raised 2026-10-09 for the batch55 prompts (Karl's call)
    written = load_prompts(jobs)
    assert len(written) == 100
    assert all(p.origin == "hand" and p.tier == "hard" and p.reference and not p.query_time for p in written)


def _results(grades: dict[str, str], types: list[str] | None = None) -> AuditResults:
    providers = [
        ProviderInfo(id=pid, label=pid, short=pid, domain="example.com", hex="#111")
        for pid in grades
    ]
    prompts, runs, judgments = [], [], []
    types = types or ["lookup"]
    for i, ptype in enumerate(types, start=1):
        prompts.append(Prompt(
            id=i, text=f"q{i}", type=ptype, reference="Sources: https://ref.example/a",
            query_time="2026-05-01" if ptype == "domain" else "",
        ))
        for pid, grade in grades.items():
            runs.append(Run(
                prompt_id=i, provider=pid, answer="a", citations=[Citation(url="https://ref.example/a")],
                tool_calls=[ToolCall(tool="fetch", urls=["https://fetched.example/x"])] if pid == "keen" else
                [ToolCall(tool="fetch")],
                fetches=1,
            ))
            judgments.append(Judgment(
                prompt_id=i, provider=pid, grade=grade, checks=[True, True, True, True], note="n",
            ))
    return AuditResults(
        meta=AuditMeta(slug="t", company="SpaceX", category="c", created_at="2026-10-08T00:00:00Z",
                       agent_model="x-ai/grok-4.7", judge_model="j"),
        providers=providers, prompts=prompts, runs=runs, judgments=judgments,
    )


def test_finished_deepseek_run_is_shown_apart_from_the_grok_block(tmp_path):
    results = _results({"exa": "c", "parallel": "w", "keen": "c", "select": "c", "routed": "w"})
    results.meta.agent_model = "deepseek/deepseek-v4.1-flash"
    results.meta.slug = "spacex"
    directory = tmp_path / "spacex"
    directory.mkdir()
    (directory / "results.json").write_text(results.model_dump_json())
    view = build_pitch(tmp_path)
    assert view["prior"]["model"] == "deepseek/deepseek-v4.1-flash"
    exa = next(row for row in view["prior"]["rows"] if row["id"] == "exa")
    assert exa["correct"] == 1 and exa["n"] == 1
    assert any("not distinguishable" in line or "fewer" in line or "more" in line for line in view["prior"]["lines"])


def test_empty_and_partial_blocks_stay_blank(tmp_path):
    view = build_pitch(tmp_path)
    from searchaudit.report.pitch import PLAN
    assert view["plan_n"] == sum(row[2] for row in PLAN)
    assert view["jobs"]["hypothesis"] == ""
    directory = tmp_path / "spacex-jobs"
    directory.mkdir()
    results = _results({"exa": "c", "keen": "c"})
    results.judgments.pop()
    (directory / "results.json").write_text(results.model_dump_json())
    assert build_pitch(tmp_path)["jobs"]["status"] == "empty"


def test_complete_block_counts_and_hypothesis(tmp_path):
    providers = ["exa", "parallel", "keen", "select", "grok", "routed"]
    grades = {pid: "c" if pid in {"keen", "select", "routed"} else "w" for pid in providers}
    results = _results(grades, ["lookup", "wide"])
    directory = tmp_path / "spacex-jobs"
    directory.mkdir()
    (directory / "results.json").write_text(results.model_dump_json())
    block = build_pitch(tmp_path)["jobs"]
    assert block["status"] == "complete"
    assert "more correct answers" in block["hypothesis"]
    keen = next(row for row in block["counts"] if row["id"] == "keen")
    assert keen["correct"] == 2 and keen["n"] == 2
    study = block["study"]
    assert len(study["charts"]["speed_cost"]["dots"]) == 6
    assert len(study["charts"]["cost_acc"]["dots"]) == 6
    assert len(study["charts"]["acc_speed"]["dots"]) == 6
    assert study["charts"]["cost_acc"]["ylabel"] == "Correct (%)"
    assert study["items"][0]["cells"][0]["answer"] == "a"
    assert study["gaps"] == []
    assert study["categories"]
    assert study["rows"][0]["partial"] == 0
    assert study["rows"][0]["searches_median"] == 0
    assert study["rows"][0]["cost_min"] == 0
    assert study["misses"] == []
    assert "no partial or wrong" in study["closing"]
    assert study["judge"] == "j"


def test_hypothesis_stays_quiet_on_a_tie():
    results = _results({"keen": "c", "select": "c", "routed": "c", "exa": "c", "grok": "w"}, ["lookup", "wide"])
    grades = {(j.prompt_id, j.provider): j.grade for j in results.judgments}
    text = _hypothesis(results, grades, ["keen", "select", "routed", "exa", "grok"])
    assert text.startswith("This run does not show")


def test_median_seconds_axis_stays_in_seconds():
    assert _tick(0, "seconds") == "0.0s"
    assert _tick(312, "seconds") == "312.0s"
    assert "m" not in _tick(312.4, "seconds")


def test_dollar_axis_fits_the_costs_and_hover_shows_prompts():
    rows = [
        {
            "id": "exa", "label": "Exa", "cls": "dot-exa",
            "per_prompt": 0.15, "t50": 40, "accuracy": 80,
            "points": [
                {"prompt": 1, "cost": 0.08, "seconds": 20, "hit": 100, "grade_word": "correct"},
                {"prompt": 2, "cost": 0.22, "seconds": 60, "hit": 0, "grade_word": "wrong"},
            ],
        },
        {
            "id": "select", "label": "SELECT", "cls": "dot-select",
            "per_prompt": 0.36, "t50": 310, "accuracy": 40,
            "points": [
                {"prompt": 3, "cost": 1.35, "seconds": 650, "hit": 0, "grade_word": "wrong"},
            ],
        },
    ]
    chart = _chart(
        rows, "per_prompt", "t50", "Dollars per prompt", "Median seconds", "usd", "seconds", "cost", "seconds",
    )
    labels = [tick["label"] for tick in chart["xticks"]]
    assert chart["xlabel"] == "Dollars per prompt"
    assert chart["ylabel"] == "Median seconds"
    assert "$0.0000" not in labels and "$0.00" not in labels and "$0.50" not in labels
    assert labels[0].startswith("$0.")
    assert all("m" not in tick["label"] for tick in chart["yticks"])
    assert len(chart["dots"][0]["pips"]) == 2
    assert chart["dots"][1]["pips"][0]["cx"] == chart["xticks"][-1]["x"]


def test_stored_spacex_run_is_gone():
    assert build_pitch(Path("audits"))["prior"] is None


def test_prompt_page_lists_missed_unique_and_fetched():
    results = _results({"exa": "c", "keen": "c"})
    results.prompts[0].reference = "Sources: https://ref.example/a https://missed.example/b"
    results.runs[0].citations = [Citation(url="https://only-exa.example/z")]
    vm = build_view_model(results)
    exa = vm["prompts"][0]["cells"]["exa"]
    keen = vm["prompts"][0]["cells"]["keen"]
    assert "https://missed.example/b" in exa["missed"]
    assert exa["only_here"] == ["https://only-exa.example/z"]
    assert keen["fetched_uncited"] == ["https://fetched.example/x"]
    assert exa["fetch_urls_reported"] is False
    assert keen["search_mode"] == ""
    dated = _results({"keen": "c", "exa": "c"}, ["domain"])
    dated_vm = build_view_model(dated)
    assert dated_vm["prompts"][0]["cells"]["keen"]["search_mode"] == "index-as-of"
    assert dated_vm["prompts"][0]["cells"]["exa"]["search_mode"] == "live search"


def test_lede_names_budget_hits_router_split_and_wins(tmp_path):
    providers = ["exa", "parallel", "keen", "select", "grok", "routed"]
    grades = {pid: "w" if pid in {"keen", "routed"} else "c" for pid in providers}
    results = _results(grades, ["lookup", "wide"])
    for run in results.runs:
        if run.provider == "keen":
            run.budget_hit = True
            run.searches = 30
        if run.provider == "exa":
            run.searches = 4
        if run.provider == "routed":
            run.via = "keen"
    # Prompt 2: only SELECT is right, so it is a Keenable win.
    for j in results.judgments:
        if j.prompt_id == 2 and j.provider in {"exa", "parallel", "grok"}:
            j.grade = "w"
    directory = tmp_path / "spacex-jobs"
    directory.mkdir()
    (directory / "results.json").write_text(results.model_dump_json())
    study = build_pitch(tmp_path)["jobs"]["study"]
    assert study["read"].startswith("Keenable SELECT 2, ")
    assert "ran out of its budget on 2 of 2" in study["read"]
    assert "median of 30 searches per prompt against Exa's 4" in study["read"]
    assert "router sent 2 of 2 to search" in study["read"]
    assert [w["id"] for w in study["wins"]] == [2]
    assert study["wins"][0]["arms"] == "Keenable SELECT"
    out = study["split"]
    assert [s["label"] for s in out] == ["Search ran out of budget"]
    assert next(c for c in out[0]["cells"] if c["id"] == "exa")["correct"] == 1
    keen = next(row for row in study["rows"] if row["id"] == "keen")
    assert keen["budget_hit"] == 2


def test_findings_only_claim_what_the_numbers_show(tmp_path):
    providers = ["exa", "parallel", "keen", "select", "grok", "routed"]
    grades = {pid: "w" if pid in {"keen", "select", "routed"} else "c" for pid in providers}
    results = _results(grades, ["lookup", "wide"])
    directory = tmp_path / "spacex-jobs"
    directory.mkdir()
    (directory / "results.json").write_text(results.model_dump_json())
    tk = build_pitch(tmp_path)["jobs"]["study"]["takeaways"]
    assert not any("beat" in line for line in tk["wins"])
    assert any("2 more" in line for line in tk["short"])
    assert any("most accurate" in line for line in tk["next"])


def test_pair_tests_are_paired_and_holm_corrected(tmp_path):
    providers = ["exa", "parallel", "keen", "select", "grok", "routed"]
    grades = {pid: "c" if pid in {"exa", "parallel", "grok"} else "w" for pid in providers}
    results = _results(grades, ["lookup"] * 12)
    directory = tmp_path / "spacex-jobs"
    directory.mkdir()
    (directory / "results.json").write_text(results.model_dump_json())
    study = build_pitch(tmp_path)["jobs"]["study"]
    tests = study["tests"]
    assert len(tests) == 9
    first = tests[0]
    assert (first["arm_id"], first["rival_id"]) == ("keen", "exa")
    assert (first["only_arm"], first["only_rival"]) == (0, 12)
    assert first["diff"] == -100
    assert first["p_acc_holm"] >= first["p_acc"]
    assert first["verdict"] == "Exa ahead"
    exa = next(row for row in study["rows"] if row["id"] == "exa")
    assert exa["ci_low"] < 100 and round(exa["ci_high"]) == 100
