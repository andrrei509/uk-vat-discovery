# CLAUDE.md — working agreement for this repo

Read this first, then `docs/ASSIGNMENT.md` (the brief, verbatim) and
`docs/PLAN.md` (the strategy).

## What this is

A technical assignment for the **Data Assets Intern** role at Veridion, by
**Andrei-Marian Dulce**. Deadline **24 Aug 2026**. Budget ~2–3 h/day.

The task: determine whether a **UK company → VAT number** dataset can be built
from the open web, and prove it on a sample. Deliverable is a single document
(`README.md`) plus supporting code, submitted as a GitHub link.

## The one rule that matters

**Veridion explicitly screens for AI-generated submissions**, and Andrei will be
cross-examined on this work in a live technical call with no assistant present.
The brief says: *"We care that the reasoning and the decisions are yours."*

So the division of labour is:

| Andrei writes | You write |
|---|---|
| All prose in `README.md` | All code in `src/` |
| Every entry in `DECISIONS.md` | Test scaffolding, data plumbing |
| The denominator estimate and its assumptions | The scripts that compute it |
| Which sources to pursue and which to abandon | The scripts that test them |
| Every conclusion and number interpretation | The scripts that produce the numbers |

**When Andrei faces a judgement call, give him options with trade-offs and let
him choose.** Do not choose for him and do not write his conclusions. If he
picks a path that fails, that is a good outcome — document it as a dead end
with evidence and move on. Dead ends are explicitly worth more to the grader
than a working scraper.

Do not write paragraphs of README prose "for him to edit". Draft prose is
sticky and uniform polish is the exact tell the grader says they screen for.
Section skeletons with bracketed notes are fine — that is what `README.md`
currently contains.

## How the work is graded

From the brief, in priority order:

1. **Part 1 (Research)** — source landscape and dead ends, *with evidence*.
   "Name the source, what you expected, and the exact reason it failed."
2. **Part 3 (Scale)** — cost per company, what breaks first, what to monitor.
   "I'd use a distributed crawler" is quoted as the anti-example.
3. **Part 2 (PoC)** — must work, on a defensible sample. Does not need to be big.

Parts 1 and 3 are stated to be "at least as important as Part 2".

## Non-negotiable engineering constraints

- **Every number in the writeup must be traceable to a file in this repo**, and
  the command that produced it must rerun from a clean clone.
- **Validity ≠ ownership.** HMRC confirming a VRN is real does *not* mean it
  belongs to the company you attached it to. A number scraped from an
  accountant's footer passes validity every time. Ownership is tested by
  comparing HMRC's returned name/address against Companies House.
- **Precision matters far more than recall.** A missing VAT number is a visible
  gap; a plausible wrong one silently corrupts every downstream join. Design
  accordingly and say so.
- **Keep the failures.** Failed source modules stay in `src/sources/`. Do not
  tidy them away — they are the deliverable.
- **Never commit `.env`.** The repo will be public. `.gitignore` covers it.
- **Raw responses before parsing.** Every HMRC call writes
  `data/raw/hmrc/<env>/<vrn>.json` before anything interprets it.

## Current state (as of 18 Aug 2026, Day 1)

**Done:**
- Repo scaffold, `README.md` skeleton, `DECISIONS.md` format.
- `src/checksum.py` — UK VAT mod-97 / mod-9755 validation + free-text
  extraction. Self-tests pass. **Measured: 4,069/200,000 random 9-digit
  strings pass the checksum = 2.03%, ~1 in 49** (not 1 in 97, because we
  accept both allocation schemes and cannot distinguish them from the number
  alone — a real precision cost, and half the answer to debate topic #1).
- `src/hmrc_client.py` — OAuth client_credentials, disk cache, backoff, raw
  response capture. **Verified working against sandbox: `authenticated=True`,
  HTTP 404 for a real VRN (sandbox holds only stub data).**
- HMRC Developer Hub sandbox application created and subscribed to
  *Check a UK VAT number*. Credentials in `.env` (untracked).

**Empirically established:**
- `/check-vat-number/lookup/{vrn}` returns **401 without a bearer token** →
  application-restricted, confirmed by running it, not just by reading the docs.
  Both raw responses (401 and 404) are in `data/raw/hmrc/sandbox/`.
- HMRC lists **Check an EORI Number** under *APIs with only open access
  endpoints* — no application, no subscription, no token.

**Resolved 18 Aug — see `notes/hmrc_api_findings.md` for citations:**
- HMRC production access for the VAT checker takes, verbatim, *"around 2 weeks.
  It may take longer if we need more information."* **The submission deadline is
  6 days away, so production VAT verification will not exist before submission.**
  This is the single most schedule-relevant fact in the project and it must be
  stated plainly in the writeup, not hidden.
- Sandbox test VRNs mirrored to `data/reference/hmrc_sandbox_test_vrns.csv`
  (40 numbers). **Only 1 of the 40 passes the mod-97/mod-9755 check digit**
  (`726129090`); HMRC's own docs example `553557881` fails it. Consequence: a
  pipeline that checksum-filters before calling HMRC cannot be end-to-end
  tested against sandbox.
- **EORI Checker is genuinely open**: the OpenAPI spec says
  `AUTHORIZATIONS: None`. `POST /customs/eori/lookup/check-multiple-eori`,
  batches of 1–10, live in production with no token.
  `src/eori_client.py` implements it.
- Companies House snapshot downloaded, profiled, hashed. See
  `audit/ch_profile_2026-08-01.txt`.

**Verification strategy — decided 19 Aug:**

| Tier | Test | How it runs | What it establishes |
|---|---|---|---|
| 1 | checksum | automated, local, free | the number is **plausible** |
| 2 | EORI checker | automated, production, no auth, batches of 10 | the number **exists** |
| 3 | HMRC web form | **MANUAL, by hand** | it **belongs to this company** |

**Decided:** do *not* automate the public web form, and do *not* rely on
production API access arriving before the 24 Aug deadline.

**Consequence:** the ownership test is manual and does not scale. That is
deliberate, and it becomes Part 3 material.

Andrei is not a VAT-registered business, so the `consultationNumber` receipt
mechanism is unavailable. **`HMRC_REQUESTER_VRN` stays blank.**

Tooling for the manual tier: `src/audit_worklist.py` generates the sheet,
`src/metrics.py` reads it back.

**Still open:**
1. Does the checksum ever **reject a real VRN**? Untested. A false *negative*
   here silently drops real numbers before any verifier sees them.
2. What share of `valid: true` EORI responses include `companyDetails`?
   **n=1 so far** — `GB220430231000` returned `valid: true` with no
   `companyDetails` block. That share bounds how much ownership testing this
   route can do.
3. Which denominator framing to use (Andrei's call, not yours) — external
   registration population, threshold-first reasoning, or frame-local only.
   External anchor available: ONS *UK Business 2025*, 14 Mar 2025 — 2.73M VAT
   and/or PAYE registered enterprises, of which 2.10M companies. Note the
   *and/or PAYE*: that makes 2.10M an upper bound, not the figure.

**Next up (Day 2):** estimate how many UK companies could plausibly hold a VAT
number (the denominator), then draw and freeze a seeded stratified sample of
~300–500. Both are described in `docs/PLAN.md` §1.1 and §5.

## Environment

- Windows, PowerShell, **Python 3.14** (some wheels may not exist yet — if
  `duckdb` won't install, rewrite the profiler against the stdlib `csv` module
  rather than fighting it).
- Repo at `C:\Users\andre\OneDrive\Desktop\uk-vat-discovery`. Note the OneDrive
  redirect: `C:\Users\andre\Desktop` is a *different* folder.
- `.env` is ASCII-encoded; the loader uses `utf-8-sig` to tolerate a BOM.

## Commands

```powershell
python src\checksum.py                                    # self-tests + measured false-pass rate
python src\hmrc_client.py 220430231 --no-consultation     # single lookup
python src\companies_house.py --to-parquet                # once, after download
python src\companies_house.py --profile                   # distributions
python src\companies_house.py --hash                      # snapshot SHA256, for reproducibility
```
