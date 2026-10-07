# TODOS

## Add Parallel as a fifth search provider (next step)
- **What:** Plugin for Parallel (parallel.ai) search behind the provider interface, plus one line in `experiments/mun.yaml`.
- **Why:** Shows the harness is a general eval tool, not an Exa-vs-Keenable one-off.
- **Pros:** Proves the "add a provider in under an hour" claim; more comparison data for Keenable.
- **Cons:** Another API key and spend; Parallel pricing/usage fields to map.
- **Context:** User asked (2026-10-06) that adding it be trivial. The plugin-interface test with a fake provider must exist first.
- **Depends on:** provider plugin interface (S6).

## Production router in Model Diplomat (deferred 2026-10-06)
- **What:** Live router choosing Exa vs Keenable per task class, with monitor, Exa shadow sample, kill switch, policy.json via PR (CEO D7, D8, D10, D14, D16; eng R1, REG1, REG2, E1–E3, E5, E6).
- **Why:** Turns benchmark results into savings in production.
- **Pros:** Real money saved; Andrey's second product idea, shipped.
- **Cons:** Production risk; needs judge calibration first.
- **Context:** Deferred when the interview artifact became the fixed-query benchmark ("way easier to test"). Full design remains in ~/.gstack/projects/KGKallasmaa-keenable-agent/impress-andrey-plan.md.
- **Depends on:** benchmark results; agent 4 (Jev routing) results as the policy prototype.

## Model Diplomat end-to-end replay (deferred 2026-10-06)
- **What:** Run Model Diplomat's real research agent with the search seam swapped (CEO D5-B replay, D13).
- **Why:** "For your agent" literally, not "an agent like yours".
- **Depends on:** Keenable ToolSeam (eng R1).

## Report: decision-document sections (deferred 2026-10-07)
- **What:** Production readiness (p95/p99, errors, rate limits), feature-parity map vs the customer's current provider, compliance/on-prem fit, migration plan.
- **Why:** These are the pieces a CTO needs before a full switch decision.
- **Context:** D1 in the report CEO review chose a polished scoreboard (A) and confirmed "keep it simple". E1 ($ at volume) and E2 (pilot) were added. Revisit after the StackAI report ships.
