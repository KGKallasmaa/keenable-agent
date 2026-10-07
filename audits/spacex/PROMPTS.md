# SpaceX (SpaceXAI / Grok) prompt set

`prompts.json` holds 60 prompts for the SpaceX audit. Each prompt has a reference answer that the judge grades against. Every reference was checked on **2026-10-07** (`as_of`).

## How the prompts were chosen

Since February 2026 SpaceX owns xAI, and Grok and X are SpaceX's AI division. Grok is a consumer assistant on X, and it answers by grounding in web and X search. So the prompts are worded the way an X user would ask Grok. They cover:

- breaking news and election results
- sports results
- earnings and deals
- tech launches
- space
- local and long-tail facts
- "what happened this week"

Some prompts are written in German, Japanese or Spanish (ids 9, 10, 18), because Grok users ask in those languages.

**Lesson from StackAI:** every provider scored 58-59/60 there, so that set could not tell providers apart. This set is built to separate them. The target is roughly 60-75% correct for a typical provider. Three design choices push it there:

- **Precision over headlines.** Answers need exact numbers, a second detail or a named runner-up: booster and ship serials, a clinching-point player, the jūryō winner, a guidance figure, an MHz range. The headline alone earns at most a partial.
- **Long-tail pages.** Several answers live on a single obscure page, such as a town magazine PDF, a municipal budget, an ordinance PDF, or a Japanese town's budget article. A model cannot answer these from memory.
- **Wide lists with grading thresholds.** Each wide prompt states how many core items are needed for correct and for partly correct.

Space, SpaceX, Starlink and xAI topics make up 15 of 60 prompts, inside the one-third cap:

- 1-4 (fresh)
- 19 and 22 (lookup)
- 36 (multihop)
- 43, 44, 48 and 52 (wide)
- 53-56 (domain)

No prompt is about Keenable, Exa, Parallel or Perplexity.

## Type mix (models.PromptType)

| Type | Count | IDs | What it tests |
|---|---|---|---|
| fresh | 18 | 1-18 | Events from 2026-09-20 to 2026-10-06 (see the list below) |
| lookup | 12 | 19-30 | Long-tail facts (see the list below). 10 of the 12 answers live mainly on German, Spanish, Japanese, French or Estonian pages |
| multihop | 10 | 31-40 | Each chain has a fresh first hop (late Sep or early Oct 2026) followed by a stable fact (see the list below) |
| wide | 12 | 41-52 | Lists with core items and a grading rule (see the list below) |
| domain | 8 | 53-60 | Exact regulatory and technical answers, mostly from 2026 primary decisions (see the list below) |

**Fresh (1-18):**
- Starship Flight 14, the first to reach orbit
- Grok 4.7 pricing
- Crew-13
- Transporter-18
- Presidents Cup
- MLB Wild Card
- Berlin Marathon
- Brazil's first round
- Berlin state election (asked in German)
- Aki basho (asked in Japanese)
- Micron and Nike earnings
- Latvia's Saeima election
- AFL and NRL Grand Finals
- Endgame retaking the box-office record
- OpenAI DevDay
- the Madrid housing march (asked in Spanish)

**Lookup (19-30):**
- mayors of Kourou, Ottobrunn and Tartu
- the Tordesillas, Taiki and Ise budgets
- the Feldkirch dog tax
- the Medellín metro fare
- the Cambridge parking permit
- the Linderhof ticket
- Mudita Kompakt specs
- the Deutscher Gründerpreis

**Multihop (31-40):**
- Baku podium → team
- Worlds runner-up → previous silver
- Schneider → PTC → CEO
- AMD → World Labs → Fei-Fei Li
- Electron → Synspective → TSE code
- Nobel Physics → birthplace → mayor
- Nobel Medicine → Max Planck institute
- Laver Cup → captain → Slam year
- C.H. Robinson → RXO → spin-off

**Wide (41-52):**
- F1 2026 race winners
- 2026 Grand Slam singles finals
- 2026 Starship flights
- China's September launches
- MLB postseason seeds
- US Open titles, including wheelchair
- Booker shortlist
- 2026 Artemis Accords signers
- Atlantic named storms
- Emmy acting winners
- Tour de France stage winners
- 2026 maiden rocket flights

**Domain (53-60):**
- FCC Amazon Leo milestone waiver
- EchoStar → SpaceX spectrum bands
- the FCC Part 100 rewrite
- Starship launch cadence at LC-39A and SLC-37
- the DSA fine split on X
- the EU AI Omnibus regulation number and dates
- Upper C-band 3.98-4.14 GHz
- the two Irish DPC inquiries into Grok and X

## Expected winners per type (hypotheses to check against results)

| Type | Expected winner | Why | Expected loser |
|---|---|---|---|
| fresh | **Keenable search / routed** (a keyword index with good freshness), then Exa | Answers need pages from the last 1-17 days. They also need exact tokens: "B21/S41", "Kitanowaka", "$61.5B", "19-18", "70.000 / 500.000". Non-English queries (9, 10, 18) reward lexical matching on local-language news. A semantic index that lags by days, or that returns evergreen explainers, misses the detail. | Parallel or Exa, if their crawl lags; SELECT (no list to build) |
| lookup | **Keenable search**, with Exa close | One obscure page holds the fact (a PDF magazine, a municipal budget, an ordinance). Exact-term matching such as "Hundeabgabe Feldkirch 2026", "令和8年度 当初予算 伊勢市" or "Erster Bürgermeister Ottobrunn" beats embedding similarity. 20, 22 and 30 are the hardest; some providers may return nothing. | SELECT (overkill); providers that are weak on non-English pages |
| multihop | **Routed / Exa / Parallel** (agentic multi-search) | The first hop is fresh and the second is stable. Wins come from correct decomposition more than from the index. Traps punish a model that skips the first hop and guesses: Borg instead of Noah (39), GXO instead of RXO (40), Red Bull instead of Racing Bulls (31). | Any provider whose first hop returns stale pages |
| wide | **Keenable SELECT** | These prompts need many rows from many pages under an explicit scope (UTC dates, attempts vs successes, a cut-off date). SELECT extracts rows directly. A plain search agent gets 5-8 rows and stops (failure mode "incomplete"). The strongest SELECT cases are 41, 44, 46, 48 and 51. On short lists (42, 47) all providers should tie. | Keenable search and Exa on the long lists, whose answers are cut short |
| domain | **Parallel / Exa** (deep, semantic retrieval over long primary documents) | Answers sit inside long FCC orders, Federal Register notices and EUR-Lex texts, so retrieving the right passage of a large PDF matters more than freshness. Keenable search should do well where the doc number is a keyword ("DA 26-553", "2026/1744", "Part 100"). SELECT should lose here. | SELECT |

Overall hypothesis: Keenable search and routed win fresh and lookup, SELECT wins wide, and Keenable loses (or ties) domain and possibly multihop. If every provider again lands at 55+/60, the set failed its purpose.

## Reference format

Each reference has four parts:

- **Answer:** the correct answer. List prompts give the full core list.
- **Partly correct:** what earns partial credit.
- **Wrong:** the typical traps, including stale answers. Examples are "Starship has not reached orbit", Avatar as the box-office record holder, Loderer as Ottobrunn's mayor, the 1,618/3,236 counts for Amazon Leo, and Borg as Laver Cup captain.
- **Sources:** 1-2 URLs checked on 2026-10-07.

Wide prompts also carry an explicit threshold rule, for example "correct = ≥N core items and no fabricated items; partly = M to N-1".

## Verification method

- Every fact was checked on 2026-10-07 with web search plus a page fetch. Primary sources were used where possible:
  - fcc.gov orders (DA 26-553, DA 26-471, FCC 26-46/47)
  - FAA and Air Force records of decision
  - eur-lex
  - formula1.com
  - city and municipal sites and PDFs (ottobrunn.de, cambridgema.gov, Feldkirch ordinance, Linderhof price list)
  - company IR pages
- Where the primary site blocked fetching (nobelprize.org, faa.gov, amd.com, some ec.europa.eu pages), the fact was cross-checked against Wikipedia or a major outlet.
- The session's web-search quota ran out near the end. After that, the remaining checks were done by fetching specific pages: raw Wikipedia text and official pages.
- Candidate prompts were dropped when sources disagreed or the event was still running:
  - Asian Games medal table: sources disagree on 4th and 5th place
  - Oura IPO: suspended 29 Sep
  - WNBA Finals: ongoing
  - September 2026 IPO and unicorn lists: unreliable counts
  - World Cup knockout scores
  - S&P 500 additions on 21 Sep: no primary S&P notice found
  - FCC 5-year deorbit rule and Starbase 25 launches a year: too easy from memory
- Overlaps between slices were removed. The Berlin Marathon and Schneider/PTC each appear only once. Starship Flight 14 appears both as fresh 1 (exact details) and inside wide 43 (the full 2026 list), on purpose.

## Known weak spots

**Fresh prompts can go stale or be revised before the run.**
- 16: Endgame vs Avatar are within about $2M, and an Avatar re-release could flip the record back.
- 9: the Berlin result is preliminary; the final could move by about 0.1 point.
- 13: Latvia's seat count is 41 per Wikipedia and 42 per LSM's preliminary count. Both are accepted.
- 44: China's September launches. A launch from 25-30 Sep is unconfirmed; the rule accepts a sourced extra launch.
- 49: Atlantic storms, if a formation date is revised.

**Weaker sourcing.** These rest on a single source or on secondary sources only:
- 4 (Transporter-18): one Spaceflight Now page
- 8 (Brazil): Rio Times-level press; the TSE was not fetched
- 18 (Madrid): local press
- 2 (Grok 4.7 date): Wikipedia plus blogs, no x.ai page
- 25 (Medellín fare): one news site; the effective date is not graded
- 57 (DSA fine split): heise; the Commission page was not fetched
- 58 (AI Omnibus): the date the regulation took effect (24 vs 27 Jul) is not graded, and the 2 Dec 2026 date for the new ban rests on FPF
- 37 (Tienen mayor): the mayor comes from VRT, Nov 2025
- 51 (Tour stages) and 42 (Slams): Wikipedia only

**Possibly answerable from memory.** These discriminate less between providers:
- 28 (Mudita Kompakt)
- 42 (2026 Grand Slams before June)
- 53-55 for a model trained after mid-2026

**Hard enough that every provider may fail:**
- 20 (Ottobrunn PDF)
- 22 and 30 (Japanese budgets)
- 10 (jūryō winner)

That is intended; a 0/5 on these is still a signal.

**Ambiguity.**
- 26 (Cambridge): the $75 fee is framed as both "from 1 Dec 2026" and "for 2027". Both are accepted.
- 52 (maiden rocket flights): counting variants is a judgment call. Core = 8 all-new vehicles; Ariane 64, Starship Block 3 and H3-30S are optional.
- 50 (Emmys): includes two limited-series supporting awards moved to the Creative Arts ceremony, a deliberate trap for lists that cover only the main telecast.
