# UK VAT Identifier Discovery

<!--
  SKELETON ONLY. Every heading below is a slot you fill with your own words.
  The bracketed notes tell you what has to be in that section and why — they
  come from the assignment text and from what the grader said they look for.
  Delete each note once you've written the section.

  Rule for the whole document: if a number appears here, a file in this repo
  produced it, and you can rerun the command that made it.
-->

**Candidate:** Andrei-Marian Dulce
**Role:** Data Assets Intern, Veridion
**Submitted:** 2026-08-24

## TL;DR

<!-- Five sentences, written LAST. What you set out to test, the single most
     important thing you found, the headline numbers, and the honest verdict on
     whether this dataset can be built. -->

## How to reproduce

### Prerequisites

Python **3.14** (developed and run on 3.14.2; `duckdb` ships a `cp314` wheel, so
no build tools are needed).

```bash
pip install -r requirements.txt
```

### The Companies House snapshot

Not committed — it is 493 MB zipped, far past what belongs in a repo. Download it
yourself:

- URL: <https://download.companieshouse.gov.uk/en_output.html>
- File: `BasicCompanyDataAsOneFile-2026-08-01.zip`
- Goes in: `data/companies_house/`

Verify you have the same snapshot:

```bash
python src/companies_house.py --hash
```

That must print:

```
file  : BasicCompanyDataAsOneFile-2026-08-01.zip
bytes : 493049031
sha256: dd625ad9b37c023c3cd8d942467d0a8348608bee15c13a4a9db1927e7ff23c21
rows  : 5695465
```

Companies House replaces this file monthly and does not archive old snapshots, so
a later download will differ. If your SHA256 does not match, every
Companies-House-derived number below will differ too, and the difference is the
snapshot, not the code.

Then convert once — everything afterwards reads the parquet and runs in seconds:

```bash
python src/companies_house.py --to-parquet
```

### HMRC credentials

`src/hmrc_client.py` needs `.env`, which is **not** committed. Create it from the
template and fill in your own credentials from the HMRC Developer Hub:

```bash
cp .env.example .env
```

`.env.example` documents every key. `HMRC_REQUESTER_VRN` is deliberately left
blank — the two-VRN endpoint that returns a `consultationNumber` receipt requires
you to be a VAT-registered business.

Nothing else in the repo needs credentials. `src/eori_client.py` calls a genuinely
open HMRC endpoint, and `src/checksum.py` and `src/metrics.py` are local.

### Traceability table

One row per number currently claimed anywhere in this repo. Numbers with **no**
reproducing command are listed separately in
[`notes/unreproducible.md`](notes/unreproducible.md) — that file is the worklist,
this table is what already stands up.

| Number | Command | Output lands in |
|---|---|---|
| `4069/200000 = 2.0345%` random 9-digit strings pass the checksum | `python src/checksum.py` | stdout (seeded `random.seed(0)`, so byte-identical every run) |
| `1/40` HMRC sandbox test VRNs pass the checksum | the one-liner at `notes/hmrc_api_findings.md:77` | stdout |
| `22` nine-digit / `18` twelve-digit sandbox VRNs | *no command* | — see `notes/unreproducible.md` §D |
| snapshot sha256 `dd625ad9…`, `493049031` bytes, `5695465` rows | `python src/companies_house.py --hash` | stdout |
| `14` of `55` CSV headers carry leading whitespace, incl. ` CompanyNumber` | `python src/companies_house.py --header` | stdout |
| `5190464` Active; `88670` no postcode; `216285` no usable SIC; `98777` SIC 99999; `38703` SIC 74990; `82123` companies at 71-75 Shelton Street | `python src/companies_house.py --profile` | `audit/ch_profile_2026-08-01.txt` |
| any other Companies House cut (frame sizes, cumulative incorporations) | `python src/companies_house.py --sql "<query>"` | stdout |
| `GB220430231000` → `valid: true`, shared trader details `0/1` | `python src/eori_client.py 220430231` | `data/raw/eori/production/GB220430231000.json`, logged to `data/raw/eori_calls.jsonl` |
| HTTP `401` `MISSING_CREDENTIALS` — proof the endpoint is application-restricted | `python src/hmrc_client.py 220430231 --no-consultation --no-auth` | `data/raw/hmrc/sandbox/220430231.noauth.single.json` |
| HTTP `404` from the sandbox VAT checker for a real VRN | `python src/hmrc_client.py 220430231 --no-consultation` **with credentials in `.env`** | `data/raw/hmrc/sandbox/220430231.json` |
| Wilson 95% CI, e.g. `47/50 → [0.837829, 0.979385]` | `python src/metrics.py --self-test` | stdout (cross-checked against an independent derivation) |
| coverage / precision / rejection breakdown | `python src/metrics.py --sheet audit/manual_audit.csv --sample <sample.csv> --candidates <raw.csv>` | stdout |
| frame size `2,362,322`, and rows removed by each exclusion | `python src/sample.py --dry-run` | stdout, and `notes/sample_design_output.txt` on a real run |
| the frozen sample of `500`, `50` held out, 20 strata | `python src/sample.py` | `data/sample/sample.csv` (seed and snapshot SHA256 in its header block) |
| the sample is byte-reproducible from its seed | `python src/sample.py --check-determinism` | stdout |
| parquet is `9.6x` faster than the CSV (`2.42s` vs `23.31s`) | `python src/companies_house.py --profile` then `--profile --no-parquet` | stdout |
| name-to-domain match rates (strong / weak / none / no_domain), split by size band and sector | `python src/domain_discovery.py` | `data/results/sample_domains.csv` and `notes/domain_discovery_output.txt`; every attempt logged to `data/raw/domain_attempts.jsonl` |
| the 20 rows drawn for hand-checking the domain method | `python src/domain_discovery.py --report-only` (seeded, no fetching) | `notes/domain_discovery_output.txt` |
| VAT numbers found on strong-match domains: domains scanned, domains yielding >=1 VRN, distinct (company, vrn), labelled vs unlabelled | `python src/sources/website_vat.py --domains data/results/sample_domains.csv --match-strength strong --out data/results/candidates.csv` | `data/results/candidates.csv` |
| the same for weak-match domains, kept separate on purpose | `... --match-strength weak --out data/results/candidates_weak.csv` | `data/results/candidates_weak.csv` |
| the 20-row domain hand-check sheet | `python src/domain_audit_sheet.py` | `audit/domain_audit.csv` |
| the manual audit sheet itself | `python src/audit_worklist.py --candidates <candidates.csv>` | `audit/manual_audit.csv` |

**One caveat on `data/raw/domain_attempts.jsonl`:** its raw line count is not the
attempt count. The log is append-only and two processes wrote to it concurrently
during one run, so **5,316 lines hold 2,785 unique attempts** — 2,531 lines are
duplicate writes of a key that was already present. Everything downstream
de-duplicates on `(company_number, candidate_domain)` at load, so the figures in
`notes/domain_discovery_output.txt` are unaffected; only `wc -l` misleads.
`notes/domain_discovery_run.md` records how it happened.

**What runs on a clean clone, and what does not.**

Runs immediately, no downloads, no credentials, no network — `checksum.py`,
`metrics.py --self-test`, the sandbox-VRN one-liner, `eori_client.py 220430231`
and `hmrc_client.py 220430231 --no-consultation --no-auth`. The last two replay
from committed cached responses rather than calling out.

Needs the Companies House snapshot downloaded first: `--hash`, `--header`,
`--profile`, `--sql`, and `audit_worklist.py` (which reads the parquet to fill in
company names and addresses). Each exits 1 with a message naming the download URL.

Needs HMRC credentials in `.env`: the authenticated `404` row. Without
credentials the same command runs in `noauth` mode and returns the `401`
instead — which is correct behaviour, not a failure, but it is a different row
of this table.

Needs inputs that do not exist yet, and so **cannot run on a clean clone at all**:

| Command | Missing input | What has to be produced first |
|---|---|---|
| `python src/metrics.py` | `audit/manual_audit.csv` | Run `audit_worklist.py` to generate the sheet, then fill in the `verdict` column by hand against HMRC's checker. |
| `python src/audit_worklist.py` | `data/results/candidates.csv` | The discovery pipeline has to run first and emit candidate `(company_number, vat)` pairs. Nothing in the repo produces this file yet. |

Both exit 1 with a message naming the file they wanted.

---

# Part 1 — Research

## 1.1 What the problem actually is

<!-- Restate the problem more precisely than the brief did. In particular:
     - what function you need (company -> VAT), and what direction the only
       verifier runs in
     - the three asymmetries: unknown denominator, asymmetric+invisible error
       cost, and "not found" being two different outcomes
     If you can state these better than they did, you have already signalled
     you understood it. -->

## 1.2 How many UK companies could even have a VAT number?

<!-- Your denominator estimate, with assumptions written out. This is the
     section almost nobody writes and it changes the meaning of every coverage
     number you report later. Show the arithmetic. Give a RANGE, not a point.
     State what would sharpen it. -->

## 1.3 Source landscape

<!-- One subsection per source you actually touched. For each:
       - what you expected before you looked
       - what you ran (command / URL / n)
       - what came back (the number)
       - verdict: usable / partially usable / dead end
     Depth should be UNEVEN. Some sources deserve four lines, one deserves two
     pages. Uniform depth across sources is the tell that nobody really
     investigated anything. -->

### 1.3.1 <source name>

## 1.4 Dead ends

<!-- The section they said is worth more than the scraper. For each dead end:
     source, expectation, exact reason it failed, and the evidence.
     "Scraping is unreliable" is not a dead end. A statute that says the field
     you hoped for isn't required, plus a count of how many publishers include
     it anyway, is. -->

## 1.5 What I learned that wasn't obvious at the start

<!-- Literally the Part 1 question. Answer it in your own voice. -->

---

# Part 2 — Proof of concept

## 2.1 Sampling design

<!-- How the sample was drawn, the seed, why it resembles the customer's
     supplier base, and — this is the part that earns credit — the specific
     ways it is NOT representative. Name your own sample's weaknesses before
     they do. The frozen sample is at data/sample/sample.csv. -->

## 2.2 Pipeline

<!-- What runs, in what order, with what filters. A diagram in text is fine.
     Say which stage throws away the most candidates and why. -->

## 2.3 Verification: validity is not ownership

<!-- HMRC confirming a number is real does NOT mean it belongs to the company
     you attached it to. Explain your name/address agreement test, and be
     honest about where it is weak (trading names, formation-agent addresses,
     fuzzy matching). Quantify your matcher's own error rate. -->

## 2.4 Results

<!-- Three numbers, not one:
       1. coverage — found/sample AND found/estimated-VAT-registered-in-sample
       2. precision — hand-audited, as a fraction, with a CI. Format it like
          "47/50" — that string is an EXAMPLE OF THE FORMAT, not a result from
          this project. Replace it with the real fraction; do not leave an
          illustrative number where a measured one belongs.
       3. rejection breakdown — how many candidates died at each stage and why
     Round numbers read as invented. Fractions read as measured. -->

## 2.5 What these numbers don't capture

<!-- Explicitly requested by the brief. Selection effects, sectors untouched,
     the fact that false negatives are unmeasurable here. -->

---

# Part 3 — What I'd do with real resources

<!-- "I'd use a distributed crawler" was quoted as the anti-example. Required:
       - the architecture, and why it changes shape at scale
       - rough cost per company acquired, with the arithmetic
       - what breaks FIRST, named specifically
       - what you'd monitor in production: concrete metrics and alert
         thresholds, not "a data quality dashboard"
       - how coverage and accuracy actually change, with numbers -->

---

## How I worked

I started off by checking for sources (hyperlinks) on the assignment page, to make sure I have everything in one place. Then analyzed the issue multiple times, and used AI to understand exactly what I'm aiming for.

AI taught me important concepts of data engineering, such as asymmetries, errors, coverage, precision and recall and much more, which I hadn't been familiar with beforehand. It also wrote the code and implemented what I needed in python scripts, and extracted the facts from the HMRC and GOV.UK pages into notes/ with citations. I caught some of its mistakes.

I decided to manually check each VAT number rather than in bulk, since API activation takes about 2 weeks, which is well past my deadline. I accepted both checksum schemes (mod-97 and mod-9755). And I chose to measure the EORI trader detail opt in rate before committing to a verification strategy, rather than guessing at it. I also opted not to automate the public web form.

I ran every command and saw the raw output : the 401 with no token, the 404 that showed authentication was working, the production EORI response, the checksum counts. I used both Claude Code and Claude, and took each one's output to the other. That caught two errors. Claude told me my email was already in the git commit metadata; Claude Code checked, and it wasn't. Claude Code recorded an HMRC quote in a wording that turned out to be two separate sentences merged; Claude had the page text and settled it.

Every number in this document has a command in the repo that produces it. If you want to test any of them, ask me and I'll run it.

---

# Debate topics

## Enumerating the checksum space against HMRC's checker

<!-- There is a technical answer and an ethical answer and they are different.
     Give both. Include the arithmetic on how many candidates actually survive
     the checksum. -->

## Keeping the dataset current

## Knowing you're wrong at scale with nothing to compare against

## Sources I would not ship

<!-- Name at least one and say what coverage you'd lose. "All of them are
     fine" is the weak answer. -->

---

# Beyond the UK

<!-- Optional. One country compared properly beats a table of 27.
     If you're short on time, DELETE this section rather than write it thin. -->

---

# Appendix

- `DECISIONS.md` — dated decision log kept during the week
- `data/raw/hmrc/` — every raw HMRC response, one JSON per VRN
- `audit/manual_audit.csv` — the hand-checked pairs behind the precision number
- `src/sources/` — one module per source tried, **including the ones that failed**
