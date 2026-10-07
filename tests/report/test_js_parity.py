"""report.js recomputes value scores when the weight sliders move; it must agree with stats.py."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from searchaudit import stats
from searchaudit.report.model import build_view_model

JS = Path(__file__).resolve().parents[2] / "searchaudit" / "web" / "static" / "report.js"
WEIGHTS = [(60, 25, 15), (100, 0, 0), (0, 100, 0), (0, 0, 100), (10, 80, 10), (0, 0, 0)]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

RUNNER = """
const SA = require(process.argv[1]);
const {data, weights} = JSON.parse(require("fs").readFileSync(0, "utf8"));
console.log(JSON.stringify(weights.map(([a, c, s]) => {
  const w = {a, c, s};
  const scores = SA.valueScores(data, w);
  const order = SA.byScore(data, scores);
  return {scores, order, headline: SA.headline(data, order),
          first: SA.promptScores(data.prompts[0].g, data.prompts[0].c, data.prompts[0].t, w)};
})));
"""


def _run_js(client: dict) -> list[dict]:
    out = subprocess.run(
        ["node", "-e", RUNNER, str(JS)],
        input=json.dumps({"data": client, "weights": WEIGHTS}),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


@pytest.mark.parametrize("which", ["fixture", "tradeoff"])
def test_js_matches_python(which, fixture_results, results_factory):
    results = (
        fixture_results
        if which == "fixture"
        else results_factory(
            {"exa": "c", "keen": "ccw", "select": "w"}, cost={"exa": 2.0, "keen": 0.01, "select": 0.01}
        )
    )
    js = _run_js(build_view_model(results)["client"])
    for (a, c, s), got in zip(WEIGHTS, js, strict=True):
        vm = build_view_model(results, stats.Weights(a, c, s))
        assert got["order"] == vm["by_score"], (a, c, s)
        for pid in vm["ids"]:
            assert got["scores"][pid] == pytest.approx(vm["summary"][pid]["score"], abs=1e-9)
        if vm["headline"]["kind"] != "insufficient":
            assert got["headline"] == vm["headline"]["text"]
        first = vm["prompts"][0]["cells"]
        assert got["first"] == pytest.approx([first[pid]["score"] for pid in vm["ids"]], abs=1e-12)
