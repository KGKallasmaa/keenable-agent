# Report metrics

Approved 2026-10-08. Replaces the stacked value-score ranking, the pairwise charts, and the 3D plot on `/r/<slug>`.

## First screen

Three sections, in order: accuracy, cost, speed. Each section is a split, then one dot per prompt on that axis only. No combined ranking. No 3D chart.

The five providers are Exa, Parallel, Keenable search, SELECT, and routed. No-search is not on the page.

## Cost

Keenable search and SELECT are priced at the list price, $0.004 per request in this audit. The page prints that price. It does not mark it as an estimate.

The cost split is dollars per prompt, search spend, model tokens, failed SELECT calls priced at $0, and dollars per correct answer. The marked cheap provider is the lowest dollars per correct answer. A wrong answer is drawn and is not marked cheap.

## Speed

Median, median among correct answers, and the slow tail (90th percentile). The marked fast provider is the lowest median among correct answers. A wrong answer is drawn and is not marked fast.

## Accuracy

Correct, partial, and wrong, out of the prompt count. The marked provider is the one with the most correct answers.

## Prompts

Under the three sections. Each row is the prompt, the type, and one cell per provider. The cell is the grade, the dollars, and the seconds. Rows sort by prompt, type, or any provider's grade, dollars, or seconds. Opening a row shows the answer. There is no best-value, best-cost, fastest, or most-accurate column.
