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
