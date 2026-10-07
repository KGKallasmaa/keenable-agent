# StackAI prompt set

`prompts.json` holds 60 prompts for the StackAI audit. Each one has a reference answer the judge grades against. All references were checked on **2026-10-07** (`as_of`).

## How the prompts were chosen

StackAI sells a no/low-code platform for building enterprise agents. Its solution pages focus on finance, insurance, healthcare, legal, PE/VC and government (stack-ai.com/solutions: insurance, insurtech, commercial brokers, actuarial consulting, growth equity, PE due diligence). From those pages we took the jobs that need web search:

- **KYC / counterparty onboarding**: identifiers (CIK, LEI, BIC), ownership, FATF lists
- **Vendor and third-party risk**: SOC 2, DORA critical providers, M&A status of vendors
- **Due diligence and investment research**: deal terms, funding rounds, IPOs, executives
- **Underwriting and insurance ops**: NAICS codes, NAIC model law
- **Compliance and regulation lookup**: EU AI Act, DORA, NIS2, GDPR, HIPAA, SEC, NYDFS
- **Macro monitoring** for treasury and credit teams: central bank decisions, CPI

The prompts are worded the way an agent built on StackAI would send them. Some carry context, such as "For our KYC file…" or "Our vendor-risk agent needs…". No prompt is about Keenable, Exa, Parallel or StackAI, to avoid a conflict of interest.

## Type mix (models.PromptType)

| Type | Count | IDs | What it tests |
|---|---|---|---|
| lookup | 16 | 1-16 | One checkable fact: an identifier, a CEO, a deal price, a fiscal year end |
| fresh | 12 | 17-28 | Facts from Apr-Oct 2026: September 2026 rate decisions at the Fed, ECB, BoE and BoJ; August CPI; the new Fed chair; Apple's new CEO; the HDFC Bank CEO successor; the Databricks round; Cognex buying RealSense; SoundHound buying LivePerson; the FATF June 2026 changes |
| multihop | 12 | 29-40 | Joining 2-3 facts from different pages, for example acquirer → CEO, parent → auditor, or a spin-out followed by an acquisition |
| wide | 10 | 41-50 | Building a list: DORA CTPPs, G-SIBs, the FATF grey list, euro members, US state privacy laws, EU AI Act Art. 5 and Annex III, GDPR adequacy, Basel members |
| domain | 10 | 51-60 | Regulatory deadlines and penalties: GDPR, HIPAA, SEC 8-K 1.05, NYDFS 500.17, DORA, the AI Act fines and the AI Omnibus, Reg S-P, NAIC #668, NIS2 |

## Reference format

Each `reference` contains three things:

1. **The answer.** For list prompts, this is the full list of required items.
2. **A grading note when one is needed.** Examples:
   - wrong answers that are outdated (Powell as Fed chair, Cook as Apple CEO, 2 Aug 2026 as the Annex III date)
   - partial-credit rules for list prompts, such as "correct = 17+ core states incl. Indiana/Kentucky/Rhode Island"
   - optional items, such as Florida, the EPO, or the new AI Omnibus bans
3. **One or two source URLs** that were checked during verification.

## Verification method

- Every fact was checked on 2026-10-07 with web search plus a page fetch. Primary sources were used where possible:
  - SEC EDGAR filings and SEC press releases
  - federalreserve.gov, ecb.europa.eu, bankofengland.co.uk, bls.gov, bis.org and fsb.org (the G-SIB annex PDF was downloaded and parsed)
  - the ESMA CTPP PDF, the GLEIF API (LEI), apple.com newsroom and the EC adequacy page
- Where the primary site blocked fetching (fatf-gafi.org, iso.org, hhs.gov, census.gov, fma.gv.at), the fact was cross-checked against two independent secondary sources. The primary URL is still listed if it is the obvious canonical source.
- Candidate prompts were dropped when the answer could not be pinned down, the topic was disputed, or the fact was likely to change within days:
  - SEC chair (term status unclear)
  - the Google–Wiz close date (sources disagree)
  - the SEC Form 10-S semiannual reporting rule (proposed, not adopted)

## Known weak spots

- **Fresh prompts can go stale before the run.** The next FOMC meeting (late Oct), the ECB (late Oct), the BoE (5 Nov), the October CPI release (mid-Oct) and the October FATF plenary may all land before the lead's run. The references for prompts 17, 21 and 28 tell the judge to accept a newer official value. Other references may need the same allowance if the run slips.
- **G-SIB lists (42, 43).** These use the Nov 2025 FSB list. A new list comes out in Nov 2026.
- **GDPR breach deadline (51).** This depends on the Digital Omnibus not being adopted. It was still in committee as of Aug 2026.
- **Weaker sourcing.** These references rest on secondary or news sources rather than a primary regulator or company page:
  - 20 (BoJ: news reports)
  - 24 (HDFC Bank: news; announced Oct 1)
  - 4 (Deutsche Bank BIC: BIC directories)
  - 46 (US state privacy count: trackers disagree on Florida, so it is treated as optional)
- **Ambiguity risks.** Prompt 2 (NAICS) and prompt 10 (SOC 2) are near common knowledge, so they test hallucination resistance more than retrieval. Prompt 50 (Basel members) lists Russia because the BIS page still does, and a model may "correct" it.
- **Thin topic coverage.** Healthcare and government get fewer prompts than finance and insurance, which reflects where StackAI's solution pages put their emphasis.

## Hard set (ids 61-100, added 2026-10-07)

All five providers scored 58-59 out of 60 on ids 1-60, so that set does not tell them apart. These 40 prompts are built to separate them, aiming for a typical provider to get about 50-70% right. Each one asks for 2-3 specific details, or for a long list with count thresholds, so the judge can give partial credit. They are written to test real differences in capability, not to favour any provider. Every reference was checked on 2026-10-07 with a web search plus a fetch of the source page: SEC 8-Ks and EDGAR, OFAC recent actions, the ESMA MiCA CASPS.csv, the FDA AI-enabled device CSV, the NAIC map, the FCA fines table, AMF and FSA documents, Handelsregister entries in company imprints and Northdata, and Crunchbase News. Candidates that could not be verified were dropped and replaced.

| Type | Count | IDs | What it tests |
|---|---|---|---|
| fresh | 12 | 61-72 | Facts from 2026-09-14 to 2026-10-02. Deals that closed: Zurich–Beazley, Long Lake–Amex GBT, Everest Canada–Wawanesa. Executive changes: Hartford CEO succession, Zendesk CFO, SAIB CEO. Rate decisions: SNB, Norges Bank and Riksbank on 24 Sep; RBA on 29 Sep. Earnings: Accenture FQ4, Micron FQ4. Funding: EliseAI, Ridgeline |
| lookup | 10 | 73-82 | Long-tail due diligence: Laser Digital Japan's FSA registration number (Japanese source), AMF sanction SAN-2026-07 (French), Aleph Alpha HRB (German), the decoy firm "Invesco Alpha Inc." (SEC), the Ken Leech settlement, the Meyer Global SEC case, the Cavalier Dynamics OFAC entry, Black Forest Labs' legal entity "BFL GmbH", Langdock HRB, and the Getsafe Broker GmbH register entry |
| wide | 12 | 83-94 | Building lists of 13-54 items. Regulator registers: FCA 2026 fines, NAIC AI bulletin adopters, French MiCA CASPs, FDA AI devices outside radiology (Apr-Jun 2026), OFAC Sinaloa designations on 29 Sep, GPAI Code signatories, ECB supervisory fines. Market maps: Q3 2026 US IPOs over $100M, US rounds of $300M+ (29 Aug-25 Sep), August 2026 unicorns, AAIF Platinum and Gold members, SPAC IPOs in Aug-Sep 2026 |
| multihop | 6 | 95-100 | 2-3 hops where at least one hop is recent or long-tail. Intermex → WU deal blocked by the California DFPI → DFPI commissioner. Delta Dental → NYDFS fine → acting superintendent. Outmarket → previous lead investor → co-founders' prior employers. Comerica → Fifth Third → auditor and auditor-since year. Cadence → Huntington → new President and his prior employer. Worldpay → Global Payments → Issuer Solutions → FIS Chief Legal Officer |

### Hypotheses (who should win, and why)

- **Fresh (61-72).** Index freshness should decide these. Most items are 1-3 weeks old, and the model's memory gives a stale answer, such as Al-Omran as SAIB CEO, the 1,230p or 1,280p Beazley offers, or a Norges Bank "hold". Whichever provider ingests news and press releases fastest should win. Search should beat SELECT here because each prompt is a single fact. If the router sends these to SELECT, the "wrong tool" failure mode should show it.
- **Lookup (73-82).** These test long-tail coverage and non-English coverage (73-75, plus the German register details in 80-82). The answer usually sits on one deep page: an FSA PDF, an AMF decision, a company imprint or Northdata, an SEC litigation release, or an OFAC action. A provider with a broad crawl and good fetching should win. Decoys test precision: 76 (Invesco Alpha is not Invesco), 80 (court is München, entity is "BFL GmbH") and 82 (three Getsafe entities). We expect the most spread across providers here, and the result may favour none of the Keenable modes.
- **Wide (83-94).** This group is the real test of SELECT. Prompts 85 (MiCA CSV), 86 (FDA CSV filtering), 87 (43 OFAC names), 90, 91 and 94 need items pulled from many pages or rows. SELECT, and routed if the router picks SELECT, should beat snippet search on item count. If they don't, structured extraction is not helping. Prompts 83, 84, 89, 92 and 93 have one aggregator page, so a provider that fetches and reads the whole page can score full marks without SELECT. That is the control within the group: it separates "finds the right page" from "extracts across many pages".
- **Multihop (95-100).** The search agent's step-by-step loop should do best here. Each hop is a narrow fact, which suits search. SELECT can join hops in one query but may fail when the later hop depends on the earlier answer. Prompts 95 and 96 have recent traps: the deal is not closed despite an aggregator page claiming it is, and the NYDFS head is "Acting" with Harris gone. Prompts 98-100 rest on EDGAR, so they favour providers that index sec.gov well.

### Least-confident prompts

- **Weak sourcing**
  - 65 (SAIB CEO): secondary sources only, and the name is transliterated differently (Al-Fughom or Al-Fugham).
  - 70 (EliseAI): secondary sources only.
  - 64 (Zendesk CFO): secondary sources only.
  - 97 (Outmarket): the investor co-founders' backgrounds come from VC-directory pages.
- **Ambiguous entity or officer**
  - 75 (Aleph Alpha): the imprint names Scheer, while Northdata also lists Spörri. The reference accepts either.
  - 82 (Getsafe): three similar entities.
  - 81 (Langdock): the only source is the company imprint.
- **Lists that may move before the run**
  - 85 (the MiCA register keeps adding firms).
  - 88 (the GPAI signatory page was updated on 7 Oct).
  - 93 (AAIF membership is growing).
  - 90: Renaissance counts 18 deals, but only 17 could be identified. The reference accepts one extra verified deal.
- **Facts that may change before the run**
  - 95 (Intermex): the deal status could change.
  - 77 (Leech): consented on 6 Oct, and court entry of the judgment may follow.
