from app.score import is_sourced, pick_winner, summarize, total_cost


def _arm(ok=True, urls=None, text="x" * 50, search_cost=0.007, tokens=100):
    return {
        "ok": ok,
        "urls": ["https://example.com"] if urls is None else urls,
        "text": text,
        "search_cost": search_cost,
        "context_tokens": tokens,
    }


def test_sourced_requires_url_and_text():
    assert is_sourced(_arm()) is True
    assert is_sourced(_arm(urls=[])) is False
    assert is_sourced(_arm(text="short")) is False
    assert is_sourced(_arm(ok=False)) is False


def test_cheaper_sourced_arm_wins():
    exa = _arm(search_cost=0.02, tokens=0)
    keen = _arm(search_cost=0.004, tokens=0)
    assert pick_winner(exa, keen, 3.0) == "keenable"


def test_unsourced_loses_even_if_free():
    exa = _arm(urls=[], search_cost=0.0, tokens=0, text="")
    keen = _arm(search_cost=0.02, tokens=0)
    assert pick_winner(exa, keen, 3.0) == "keenable"


def test_close_costs_tie():
    exa = _arm(search_cost=0.010, tokens=0)
    keen = _arm(search_cost=0.0105, tokens=0)
    assert pick_winner(exa, keen, 3.0) == "tie"


def test_context_tokens_can_flip_the_winner():
    exa = _arm(search_cost=0.004, tokens=0)
    keen = _arm(search_cost=0.004, tokens=2_000_000)
    assert total_cost(keen, 3.0) > total_cost(exa, 3.0)
    assert pick_winner(exa, keen, 3.0) == "exa"


def test_summary_cost_per_sourced():
    tasks = [
        {
            "winner": "keenable",
            "exa": _arm(urls=[], text="", tokens=0, search_cost=0.007),
            "keenable": _arm(search_cost=0.008, tokens=0),
        }
    ]
    summary = summarize(tasks, 3.0)
    assert summary["exa"]["cost_per_sourced"] is None
    assert summary["keenable"]["sourced"] == 1
    assert summary["wins"]["keenable"] == 1
