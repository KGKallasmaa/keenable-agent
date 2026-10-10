import json

import pytest

from searchaudit.gap.assemble import assemble, sql_ok
from searchaudit.gap.log import (
    _assistant_for,
    micro_cents_to_usd,
    require_columns,
    row_from_turn,
    tag_class,
    write_jsonl,
)
from searchaudit.gap.loss import label_miss, render_csv
from searchaudit.gap.sample import stratified


def test_micro_cents_are_the_trace_total():
    assert micro_cents_to_usd(870081) == 0.870081
    assert micro_cents_to_usd(None) is None


def test_row_from_turn_keeps_the_five_columns_and_the_trace_dollars():
    turn = {
        "_id": "run-1", "thread_id": "t1", "query": "What did the committee decide",
        "cost_mc": 500000, "is_current_events": False,
        "turn_started_at": "2026-06-15T16:06:11",
    }
    thread = {"messages": [
        {"role": "user", "message": {"query": "What did the committee decide about the draft"}},
        {"role": "assistant", "message": {
            "answer": {"text": "They adopted it."},
            "steps": [
                {"kind": "tool", "label": "Searching the web for committee draft decision",
                 "icon": "globe", "arguments": {"query": "committee draft decision 2026"}},
            ],
            "search_context": json.dumps({"sources": [{"url": "https://example.com/a", "title": "Committee adopts draft"}]}),
        }},
    ]}
    row = row_from_turn(turn, thread)
    require_columns(row)
    assert row["provider"] == "exa"
    assert row["dollars"] == 0.5
    assert row["queries"] == ["committee draft decision 2026"]
    assert row["class"] == "fact lookup"
    assert row["urls"][0]["url"] == "https://example.com/a"


def _turn(thread_id="t1", query="What did the committee decide", cost_mc=500000):
    return {
        "_id": "run-1", "thread_id": thread_id, "query": query,
        "cost_mc": cost_mc, "is_current_events": False,
        "turn_started_at": "2026-06-15T16:06:11",
    }


def _assistant_message():
    return {"role": "assistant", "message": {
        "answer": {"text": "They adopted it."},
        "steps": [
            {"kind": "tool", "label": "Searching the web for committee draft decision",
             "icon": "globe", "arguments": {"query": "committee draft decision 2026"}},
        ],
        "search_context": json.dumps({"sources": [{"url": "https://example.com/a", "title": "Committee adopts draft"}]}),
    }}


def test_row_from_turn_skips_a_non_dict_message_in_the_outer_loop():
    turn = _turn()
    thread = {"messages": [
        None,  # non-dict entry encountered by the outer loop
        {"role": "user", "message": {"query": "What did the committee decide about the draft"}},
        _assistant_message(),
    ]}
    row = row_from_turn(turn, thread)
    require_columns(row)
    assert row["queries"] == ["committee draft decision 2026"]
    assert row["urls"][0]["url"] == "https://example.com/a"


def test_assistant_for_skips_a_non_dict_message_between_user_and_assistant():
    thread = {"messages": [
        {"role": "user", "message": {"query": "What did the committee decide about the draft"}},
        None,  # non-dict between the user message and the assistant message
        "stray",  # also non-dict
        _assistant_message(),
    ]}
    text, assistant = _assistant_for(thread, "What did the committee decide")
    assert text.startswith("What did the committee decide")
    assert assistant["answer"]["text"] == "They adopted it."


def test_export_loop_proceeds_past_a_thread_with_non_dict_messages(tmp_path):
    bad_thread = {"messages": [None]}
    good_thread = {"messages": [
        {"role": "user", "message": {"query": "What did the committee decide about the draft"}},
        _assistant_message(),
    ]}
    threads = {"t1": bad_thread, "t2": good_thread}
    turns = [
        _turn(thread_id="t1"),
        _turn(thread_id="t2"),
        _turn(thread_id="t1"),
    ]
    rows = []
    for t in turns:  # mirrors export_diplomat.export_log's plain for-loop
        row = row_from_turn(t, threads.get(str(t["thread_id"])))
        if row:
            rows.append(row)
    # The two bad-thread turns are dropped; the one good-thread turn exports.
    assert len(rows) == 1
    assert rows[0]["queries"] == ["committee draft decision 2026"]
    # write_jsonl is reached (the bug aborted before this); the file is created and readable.
    dest = tmp_path / "log.jsonl"
    assert write_jsonl(dest, rows) is None
    assert dest.exists()
    lines = [json.loads(line) for line in dest.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["queries"] == ["committee draft decision 2026"]


def test_tag_class_covers_the_six():
    assert tag_class("latest vote on the bill") == "news/recency"
    assert tag_class("how to file a position paper") == "how-to"
    assert tag_class("compare the two drafts") == "list/compare"
    assert tag_class("who is the rapporteur") == "entity profile"
    assert tag_class("What year was the treaty signed") == "fact lookup"
    assert tag_class("the obscure subcommittee footnote about annex III implementation details in the working group") == "long-tail"


def test_a_followup_joined_to_another_search_is_not_a_row():
    from searchaudit.gap.log import coherent

    assert not coherent({
        "task": "some easy pois for india",
        "queries": ["countries with highest female pretrial detention rates"],
    })
    assert coherent({
        "task": "draft an opening speech for australia on the health regulations",
        "queries": ["Australia International Health Regulations treaty 2025"],
    })


def test_label_miss_is_index_gap_or_extraction_miss():
    assert label_miss("https://a.test/x", set(), value_wrong=False) == "index gap"
    assert label_miss("https://a.test/x/", {"https://a.test/x"}, value_wrong=True) == "extraction miss"
    assert label_miss("https://a.test/x", {"https://a.test/x"}, value_wrong=False) is None


def test_loss_csv_rejects_an_unlabeled_url():
    with pytest.raises(ValueError):
        render_csv([{"task": "t", "url": "https://a.test", "title": "", "label": "", "class": "fact lookup", "dollars": 0.1}])


def test_sql_check_rejects_the_raw_sentence_and_a_parse_error():
    assert not sql_ok([{"tool": "select", "args": {"query": "CONSORT 2025 statement"}, "error": ""}])
    assert not sql_ok([{"tool": "select", "args": {"query": "SELECT url FROM WEB_SEARCH('x')"}, "error": "SQL parse error: no"}])
    assert sql_ok([{"tool": "select", "args": {"query": "SELECT url FROM WEB_SEARCH('x')"}, "error": ""}])


def test_sample_weights_back_to_the_log_and_hides_a_tiny_class(tmp_path):
    rows = []
    for i in range(12):
        rows.append(_row(i, "fact lookup"))
    for i in range(4):
        rows.append(_row(100 + i, "how-to"))
    sample, weights = stratified(rows, per_class=30, seed=1)
    assert len([r for r in sample if r["class"] == "fact lookup"]) == 12
    assert weights["fact lookup"] == 1
    assert len([r for r in sample if r["class"] == "how-to"]) == 4
    log = tmp_path / "log.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps({
        "turn_id": rows[0]["turn_id"], "grade": "c", "cost_usd": 0.02,
        "answer": "Committee adopts draft",
        "tool_calls": [{"tool": "select", "args": {"query": "SELECT title FROM WEB_SEARCH('x')"}, "error": "", "urls": []}],
    }) + "\n")
    payload = assemble(log, runs, tmp_path)
    fact = next(c for c in payload["classes"] if c["class"] == "fact lookup")
    howto = next(c for c in payload["classes"] if c["class"] == "how-to")
    assert fact["match_rate"] == 1.0
    assert howto["rate_shown"] is False
    assert payload["opened"]["task"] == rows[0]["task"] or payload["opened"]["task"] in {r["task"] for r in rows}
    assert "elicit" not in payload["opened"]["task"]
    assert "index gap" in (tmp_path / "loss.csv").read_text()


def _row(i: int, kind: str) -> dict:
    return {
        "task": f"task {i} about a real committee",
        "queries": [f"committee decision {i}"],
        "provider": "exa",
        "dollars": 0.2,
        "timestamp": "2026-06-15T00:00:00",
        "class": kind,
        "urls": [{"url": f"https://example.com/{i}", "title": "Committee adopts draft"}],
        "answer": "They adopted it.",
        "turn_id": f"t{i}",
    }
