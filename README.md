# UK VAT Identifier Discovery

<!--
  SKELETON ONLY. Every heading below is a slot you fill with your own words.
  The bracketed notes tell you what has to be in that section and why. They
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

Not committed, it is 493 MB zipped, far past what belongs in a repo. Download it
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

Then convert once, everything afterwards reads the parquet and runs in seconds:

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
blank, the two-VRN endpoint that returns a `consultationNumber` receipt requires
you to be a VAT-registered business.

Nothing else in the repo needs credentials. `src/eori_client.py` calls a genuinely
open HMRC endpoint, and `src/checksum.py` and `src/metrics.py` are local.

### Traceability table

One row per number currently claimed anywhere in this repo. Numbers with **no**
reproducing command are listed separately in
[`notes/unreproducible.md`](notes/unreproducible.md), that file is the worklist,
this table is what already stands up.

| Number | Command | Output lands in |
|---|---|---|
| `4069/200000 = 2.0345%` random 9-digit strings pass the checksum | `python src/checksum.py` | stdout (seeded `random.seed(0)`, so byte-identical every run) |
| `1/40` HMRC sandbox test VRNs pass the checksum | the one-liner at `notes/hmrc_api_findings.md:77` | stdout |
| `22` nine-digit / `18` twelve-digit sandbox VRNs | *no command* | see `notes/unreproducible.md` §D |
| snapshot sha256 `dd625ad9…`, `493049031` bytes, `5695465` rows | `python src/companies_house.py --hash` | stdout |
| `14` of `55` CSV headers carry leading whitespace, incl. ` CompanyNumber` | `python src/companies_house.py --header` | stdout |
| `5190464` Active; `88670` no postcode; `216285` no usable SIC; `98777` SIC 99999; `38703` SIC 74990; `82123` companies at 71-75 Shelton Street | `python src/companies_house.py --profile` | `audit/ch_profile_2026-08-01.txt` |
| any other Companies House cut (frame sizes, cumulative incorporations) | `python src/companies_house.py --sql "<query>"` | stdout |
| `GB220430231000` → `valid: true`, shared trader details `0/1` | `python src/eori_client.py 220430231` | `data/raw/eori/production/GB220430231000.json`, logged to `data/raw/eori_calls.jsonl` |
| HTTP `401` `MISSING_CREDENTIALS`, proof the endpoint is application-restricted | `python src/hmrc_client.py 220430231 --no-consultation --no-auth` | `data/raw/hmrc/sandbox/220430231.noauth.single.json` |
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
| the extraction split: of the 33 strong domains that yielded no VRN, `32` had no 9-digit candidate at all and `1` had one that failed the check digit | `python src/sources/website_vat.py --domains data/results/sample_domains.csv --match-strength strong --out data/results/candidates.csv --cache-only --no-write` | `notes/extraction_breakdown.txt`. Reads `data/raw/pages`, which is gitignored, so a clean clone must re-crawl first |
| the method's ceiling: publication rate `6/39`, share of the 500 with a findable website `0.599`, ceiling `9.2%` with range `1.7%` to `26.1%` | `python src/method_ceiling.py` | `notes/method_ceiling.txt` |
| the 20-row domain hand-check sheet | `python src/domain_audit_sheet.py` | `audit/domain_audit.csv` |
| the manual audit sheet itself | `python src/audit_worklist.py --candidates <candidates.csv>` | `audit/manual_audit.csv` |

**One caveat on `data/raw/domain_attempts.jsonl`:** its raw line count is not the
attempt count. The log is append-only and two processes wrote to it concurrently
during one run, so **5,316 lines hold 2,785 unique attempts**, 2,531 lines are
duplicate writes of a key that was already present. Everything downstream
de-duplicates on `(company_number, candidate_domain)` at load, so the figures in
`notes/domain_discovery_output.txt` are unaffected; only `wc -l` misleads.
`notes/domain_discovery_run.md` records how it happened.

**What runs on a clean clone, and what does not.**

These run immediately, with no downloads, no credentials and no network:
`checksum.py`, `metrics.py --self-test`, the sandbox-VRN one-liner,
`eori_client.py 220430231` and
`hmrc_client.py 220430231 --no-consultation --no-auth`. The last two replay
from committed cached responses rather than calling out.

Needs the Companies House snapshot downloaded first: `--hash`, `--header`,
`--profile`, `--sql`, and `audit_worklist.py` (which reads the parquet to fill in
company names and addresses). Each exits 1 with a message naming the download URL.

Needs HMRC credentials in `.env`: the authenticated `404` row. Without
credentials the same command runs in `noauth` mode and returns the `401`
instead, which is correct behaviour, not a failure, but it is a different row
of this table.

Needs inputs that do not exist yet, and so **cannot run on a clean clone at all**:

| Command | Missing input | What has to be produced first |
|---|---|---|
| `python src/metrics.py` | `audit/manual_audit.csv` | Run `audit_worklist.py` to generate the sheet, then fill in the `verdict` column by hand against HMRC's checker. |
| `python src/audit_worklist.py` | `data/results/candidates.csv` | The discovery pipeline has to run first and emit candidate `(company_number, vat)` pairs. Nothing in the repo produces this file yet. |

Both exit 1 with a message naming the file they wanted.

---

# Part 1: Research

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

| Source | What I expected | What I ran | What came back | Verdict |
|---|---|---|---|---|
| HMRC Check a UK VAT Number API v2.0 | | `hmrc_client.py --no-auth` | HTTP 401, application-restricted; production access "around 2 weeks" | Dead end on this timeline, §1.4 |
| HMRC sandbox | | `checksum.py` on the 40 published test VRNs | 1 of 40 pass the check digit | Proves plumbing only, §1.4 |
| HMRC Check an EORI Number API | | `eori_client.py`, 8 candidates | 5 valid; 2 of 5 shared trader details | Partially usable: existence yes, ownership rarely |
| HMRC VAT checker web form | | 8 lookups by hand | name and address for every one | Usable, and the only ownership test I had |
| Companies House bulk snapshot | | `companies_house.py --profile` | 5,695,465 rows, 55 columns, no website field | Usable as the frame, useless as a source, §1.4 |
| Company websites, domains guessed from names | | `domain_discovery.py`, 2,785 attempts | 39 strong of 500, 83.8% died at DNS | The PoC's actual source |
| Weak-matched domains | | `website_vat.py --match-strength weak` | 2 VRNs from 125 domains, 0 of 2 owned | Dead end, §1.4 |
| Search-engine AI overviews | | 5 lookups while hand-checking | contradicted my own Companies House snapshot twice, wrong company number once | A lead, never evidence |

## 1.4 Dead ends

### HMRC sandbox as a test environment

I expected to test the whole pipeline end to end against the HMRC sandbox before
production access arrived. I managed to pull HMRC's 40 published mock VRNs
(`data/reference/hmrc_sandbox_test_vrns.csv`), tested them through
`src/checksum.py`, and the result was 1 out of 40 passing the mod-97/mod-9755
check, with HMRC's own documentation example (553557881) failing. My pipeline
checksums before calling HMRC, so it would discard 39 of their 40 test cases
before 1 single request went out. The thing with the sandbox is that it can prove
my plumbing and nothing else, so every real number has to be verified elsewhere.

### HMRC production API access

I expected the whole process to be simple: apply, get some credentials and verify
in bulk. The thing is, "Registering should take around 2 weeks. It may take
longer if we need more information.", which I didn't have time for. Instead, I
went with the EORI API that checks existence, and used the HMRC web form by hand
for ownership. Not having the official verifier was a conundrum until I thought
of using the web form, which is not ideal in terms of time, but still better than
nothing.

### Companies House has no website field

What I was expecting before delving into the Companies House registry is for it
to give me some route to company websites. I profiled all 55 columns
(`python src/companies_house.py --profile`) but to my surprise, no website field.
URI is a Companies House URL, not a company's. And it mattered a lot, since
company -> website was clearly an unsolved prerequisite before I could even start
looking for a VAT number. So before I could look for a single VAT number, I had
to make up a way to find company websites, and the next section states how well
that worked.

### Guessing domains from registered names

Initially, what I was aiming for was simple: normalize the registered name, try
.com/.uk/.co.uk and find the site. I ran 2,785 candidate domains across 500
companies with `python src/domain_discovery.py`. I then found 39 confident
matches, a percentage of 7.8%. 83.8% of attempts died at DNS. The thing is, my
hand check of 20 suggests roughly half the sample actually has a website, so it
wasn't data being absent, but my method failing. That estimate is soft. My audit
checked 5 rows from each outcome group instead of drawing proportionally, so the
figure leans on 3 of 5 rows standing in for 295 companies. It points at the right
conclusion, but I wouldn't quote it as a measurement. Four reasons (false
negatives) it missed real sites: shortened trading form (Odyssey), different
trading name (MPL Contractors), HTTP 403 (AJE Tech), and rebrand + redirect (Did
Teach). And one where it did the opposite, found a site and attributed it to the
wrong legal entity: the subsidiary trap (PHSC). (this was a false positive)

### Weak name matching as a usable signal

At first, I thought to myself, if a page contains the company's distinctive word,
it's probably their website. After crawling all 125 weak domains into a separate
file `--match-strength weak`, never merged with the strong ones, I discovered 2
VRNs from 125 domains (1.6%), against 6 from 39 strong (15.4%). Both weak numbers
belonged to other companies, so 0/2 owned. The reason why it's broken is due to
it being three times the strong crawling (125 domains vs. 39), with zero correct
pairs. What it cost to exclude? Well, ADVANCED ROOF TESTING was classified weak
and is genuinely their site. At least one true positive thrown away, so the
exclusion wasn't free.

### EORI as the ownership test

Initially, I hypothesized that EORI returns the trader's name, so ownership would
come free and automated. After running all 8 candidates through
`src/eori_client.py`, I found 5 valid, but only 2 of the 5 included
companyDetails. This is because traders opt in to publishing their name, and most
of them don't. I also found two of the three that came back invalid (ECONOSTORE,
TERRASTRUCT), yet HMRC says both numbers are real and theirs. An EORI "false"
means the company has no EORI, not that the number is fake. Used as a filter it
would have discarded two correct pairs. I asked myself why this is a partial dead
end, and reached the conclusion that it confirms existence cheaply and at scale,
but usually can't confirm ownership. That eventually fell back to the web form,
by hand.

## 1.5 What I learned that wasn't obvious at the start

<!-- Literally the Part 1 question. Answer it in your own voice. -->

---

# Part 2: Proof of concept

## 2.1 Sampling design

<!-- How the sample was drawn, the seed, why it resembles the customer's
     supplier base, and, this is the part that earns credit, the specific
     ways it is NOT representative. Name your own sample's weaknesses before
     they do. The frozen sample is at data/sample/sample.csv. -->

## 2.2 Pipeline

```
Companies House snapshot          5,695,465 rows
  └─ exclusions                   frame 2,362,322
      └─ stratified sample, seed 20260820, 20 strata
                                  500  (+50 held out, untouched)
          └─ domain_discovery.py  strong 39 | weak 125 | none 41 | none found 295
              └─ FILTER: strong only                    39   ← 461 lost here
                  └─ website_vat.py, 15 paths per site, robots.txt obeyed
                      └─ VAT number present on the site  6   ← 33 lost here
                         (32 had no 9-digit candidate at all,
                          1 had one that failed the check digit)
                          └─ EORI existence check, NOT used as a filter
                                                         6   ← 0 lost here
                             (as a filter: 4, and both lost
                              were confirmed correct)
                              └─ HMRC web form by hand, ownership
                                                         6   ← 0 lost here
```

## 2.3 Verification: validity is not ownership

<!-- HMRC confirming a number is real does NOT mean it belongs to the company
     you attached it to. Explain your name/address agreement test, and be
     honest about where it is weak (trading names, formation-agent addresses,
     fuzzy matching). Quantify your matcher's own error rate. -->

## 2.4 Results

| Stage | In | Out | Lost | Where the number lives |
|---|---|---|---|---|
| Sample drawn, seed 20260820, 20 strata | frame 2,362,322 | 500 | 50 held out | `data/sample/sample.csv` |
| Domain discovery, strong matches only | 500 | 39 | 461 | `data/results/sample_domains.csv` |
| VAT number present on the site | 39 | 6 | 33 (32 none, 1 check digit) | `notes/extraction_breakdown.txt` |
| EORI existence, not used as a filter | 6 | 6 | 0 | `data/raw/eori/production/` |
| HMRC web form, ownership | 6 | 6 | 0 | `audit/manual_audit.csv` |

End to end: 6 of 500 = 1.2%

Precision on shipped rows: 6/6, Wilson 95% CI [0.610, 1.000]

Weak-match control, excluded: 2 found, 0/2 owned

Before I ran it, I expected it to be higher than this, because I thought that out of 500 companies I would end up with more than 6. I started with 500 companies and ran a test to see if I could find their website. 295 of them had no domain at all, and another 166 had one I didn't trust enough to count, so 461 dropped out and the remaining 39 moved on. Out of those 39 that were asked if the site shows a VAT number, 33 said no, so I only had 6 candidates left. The 1.2% coverage is biased low, and these 3 mechanisms dragged it down: 4 false negatives in 20 hand checked domain assignments, 295 companies never crawled at all because no domain was found, and only 15 candidate paths per site, which is how AJE Tech, a site I knew was correct, came back 403. I held back 50 rows, drawn with the sample, and never touched them. The experiment I would do is to work those 50 by hand, find each site myself, look for the VAT number myself, and compare against what the pipeline gets on the same 50. The gap between the 2 is the miss rate. I didn't have the week to run it.

I checked 8 pairs by hand against HMRC's VAT checker web form, each pair being a VAT number and the company I had attributed it to. 6 were strong matches and all 6 returned the right company. 2 were weak matches and both returned a different company. 6 out of 6 isn't the same as saying the method is always right, because the Wilson 95% interval on this rate is 0.61 to 1.00. If the true rate were 0.61, the probability of getting all 6 right in a row is 0.61 raised to the 6th power, which is about 5%. That is the usual line for too unlikely to keep believing, so 0.61 is roughly the lowest true rate still compatible with what I saw. If the true rate were higher, say 0.8, which is where my own domain audit lands with 4 of 5 strong assignments correct, the chance of getting 6 in a row would be 26%, which is not surprising at all. As for what to expect on the next hundred strong rows, a wrong domain almost guarantees a wrong number, and a right domain does not guarantee a right one, so 80% is a ceiling and not a target. That 4 of 5 is itself only five rows, so it is a soft anchor. What I actually expect is to land at or below 80%, not at 6 out of 6.

Of the 39 companies whose sites I crawled, 33 were lost at extraction. 32 of them had no 9-digit number anywhere on the site, and 1 of them had one that failed the check digit, ecorpconsulting.co.uk, so my code removed 1 of the 33, and the companies removed 32 by not publishing. A better crawler wouldn't have fixed that, since on the 15 pages I fetched there was no VAT number to find at all.

494 companies were removed before anything reached the web form, 461 at domain discovery and 33 at extraction, so the 6 that arrived had already survived a strict filter. A verifier that only ever sees pre-filtered input passes everything, whether it works or not. The 2 weak candidates were a separate control and were not among the 6. Both went through the web form and both came back as a different company, which is the evidence that the verifier works, because they were the only input it saw that failed the strong-match test. The thing is, EORI returned `valid: false` for 2 of the 6 strong candidates, ECONOSTORE and TERRASTRUCT, and the web form confirmed both were genuinely owned. EORI was never used as a filter. Had it been, it would have destroyed 2 of the 6 correct rows.

## 2.5 What these numbers don't capture

<!-- Explicitly requested by the brief. Selection effects, sectors untouched,
     the fact that false negatives are unmeasurable here. -->

---

# Part 3: What I'd do with real resources

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

- `DECISIONS.md`, dated decision log kept during the week
- `data/raw/hmrc/`, every raw HMRC response, one JSON per VRN
- `audit/manual_audit.csv`, the hand-checked pairs behind the precision number
- `src/sources/`, one module per source tried, **including the ones that failed**
