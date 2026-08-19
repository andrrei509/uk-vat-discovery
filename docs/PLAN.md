# Strategy — UK VAT Identifier Discovery

Written 17 Aug 2026. Working notes, not a contract. Where reality disagrees with
this document, reality wins and the disagreement goes in `DECISIONS.md`.

Every hypothesis below is **untested unless marked otherwise**. Several are
probably wrong. The wrong ones are the valuable ones — they are Part 1.

---

## 1. The problem, stated precisely

We need a function `company → VAT number`. What exists is:

- **A registry with no VAT numbers.** Companies House free snapshot; name,
  number, registered address, SIC, incorporation date, accounts category.
  **No websites. No VAT numbers.**

  Sizes as written here on 17 Aug were ~5.4M companies / ~4.2M live. **Measured
  on the 2026-08-01 snapshot: 5,695,465 rows, all live, of which 5,190,464 are
  `CompanyStatus = 'Active'`** (`python src/companies_house.py --hash` and
  `--profile`). The ~4.2M is *the assignment brief's* figure, not a measurement
  of this snapshot; both are kept above so the gap is visible.
- **A verifier that only runs backwards.** HMRC takes a VAT number and returns
  the registered name + address. It cannot take a company and return its number.
- **No ground truth.** No list of (company, VAT) pairs to score against.

### 1.1 Three asymmetries that define the task

**(a) The denominator is unknown.** The brief's figures: ~4.2M live companies vs
~2.18M VAT registrations *including sole traders and partnerships that aren't
companies at all*. So the number of **companies** with a VAT number is
materially below 2.18M.

Neither figure is measured here. The snapshot's own count is **5,695,465 live /
5,190,464 Active**, which is ~1.5M above the brief's 4.2M — so a ratio built on
4.2M is not a ratio about this snapshot. Which denominator to use is the open
question, not a settled number.

Consequence: **coverage is meaningless without a denominator.** "I found 18% of
my sample" is a bad number. "I found 18% of my sample, and I estimate X% of that
sample is VAT-registered at all, so I recovered ~Y% of the recoverable" is the
number they want. Deriving that estimate — from the £90k registration threshold,
accounts filing category, dormancy flags, company age, SIC sector — is real
analytical work and almost nobody does it.

**(b) Errors are asymmetric and invisible.** A missing VAT number is a visible
gap. A *plausible* VAT number attached to the wrong company silently corrupts
every downstream join, forever, and surfaces in an audit. The loss function is
not F1: **precision must be near-perfect; recall is negotiable.** Say this
explicitly and let it drive the design.

**(c) "Not found" is two different outcomes.** Not registered vs. failed to find.
If you can't separate them you can't tell the customer anything useful. Even a
crude separator (a classifier over Companies House features predicting "should be
VAT-registered") turns a useless output into a sellable one — the customer can
act on "this supplier probably has one and we're missing it".

---

## 2. Source landscape

### Tier A — Verification (not discovery)

| Source | Status | Notes |
|---|---|---|
| **HMRC Check a UK VAT Number API v2.0** | ✅ working (sandbox) | `/check-vat-number/lookup/{vrn}` and `/lookup/{vrn}/{requesterVrn}`. The second returns a **`consultationNumber`** — a timestamped receipt proving you checked. Application-restricted: confirmed empirically (401 without a bearer token). Sandbox returns stubs only. |
| **HMRC EORI Checker API** | ✅ open access (per HMRC's own listing) | `GB EORI = GB + <9-digit VAT> + 000`. So this is a **second, independent verifier requiring no auth**, and any published EORI is a *free VAT number*. Test the relationship before believing it. |
| **VIES** | untested | EU checker. Post-Brexit, only **XI** (Northern Ireland) numbers should be in VIES; GB numbers should not. Worth stating — it kills a route most people assume works. Verify. |

### Tier B — Discovery: structured / bulk

| Source | Expectation (untested) | What to measure |
|---|---|---|
| **Company websites** (footers, T&Cs, contact, invoice PDFs) | The main reservoir. UK e-commerce rules push online sellers to publish it. | Hit rate *conditional on having a website*. Note the hidden prerequisite: Companies House gives you no website (§3 Path C). |
| **Common Crawl** | The right *shape* for this problem (§3 Path A). | VAT-pattern hits per GB of WET text; share resolvable to a Companies House name. |
| **Local authority spend-over-£500 files** | ⚠️ The Local Government Transparency Code 2015 appears **not** to require the supplier's VAT number — required fields look like date, department, beneficiary, purpose, amount, irrecoverable VAT, merchant category. Some councils publish it voluntarily. | **Confirm the statute yourself.** Then check ~15 councils' actual files and count how many include a VAT column. A confirmed partial dead end with a citation beats a vague one. |
| **Contracts Finder / Find a Tender** | Supplier name + sometimes registration details for contracts >£5k. Probably not VAT. | Measure actual VAT presence in the API/bulk dumps rather than assuming. |
| **The Gazette** (insolvency notices) | Liquidator statements of affairs sometimes carry VAT numbers. Biased sample (dying companies). | Cheap to test; note the survivorship bias — a dataset of insolvent suppliers is nearly worthless to a procurement team. |
| **Online marketplace seller pages** | Legally-driven VAT display for marketplace sellers. High density. | **This is the honest answer to "which source would you not ship?"** — ToS/product risk. |
| **Sector registers** (Charity Commission, FCA, AWRS, gambling, food) | Mostly the *wrong* identifier (URN, FRN, charity number). | One quick check each; short paragraph ruling them out, naming the identifier they actually publish. |
| **Commercial aggregators** (Endole, vat-search-style sites) | They have the data; licensing/ToS makes them unusable as a source you resell. | One paragraph: their existence proves the data is compilable; their terms are why the customer can't just buy it. |

### Tier C — Adjacent identifiers

The strongest under-explored route, and the brief hints at it directly.
`GB EORI = GB + VAT + 000` means **anywhere an EORI is published, a VAT number is
published**. EORIs surface in customs paperwork, shipping/freight documentation,
importer listings, and some product-compliance/UKCA declarations.

Even if the yield is small, *demonstrating the relationship and testing it* is
exactly the "figure out whether this data can be acquired at all" judgment.

---

## 3. Three strategic paths

The brief drops a hint worth taking seriously:

> "Bulk web corpora — if the numbers are scattered across millions of pages,
> crawling site by site may be the wrong shape entirely."

### Path A — Corpus-first (invert the problem)

Don't ask "what is this company's VAT number?" Ask **"which VAT numbers exist on
the web, and who do they belong to?"**

```
Common Crawl text  →  regex for GB VAT patterns + checksum filter
                   →  (vat, domain, page text) triples
                   →  extract company name near the number / from the domain
                   →  match name+address → Companies House
                   →  verify vat → HMRC → does returned name match?
```

- **Why it's right:** removes the domain-discovery prerequisite entirely. You
  find the number first and identify the owner second. Scales with corpus size,
  not company count.
- **Why it's hard on a laptop:** full CC is ~100 TB. **Don't process it — sample
  it.** Pull a handful of random WET files (~150 MB gzipped each), measure *VAT
  hits per GB* and *share resolvable to a company*, then extrapolate with an
  explicit cost model. That bounded experiment writes Part 3 for you.

### Path B — Registry-first

Mine structured public datasets (council spend, procurement, Gazette, sector
registers). Clean, verifiable, legally safe, **low coverage and biased** (only
companies that sell to government or go insolvent). Its value in the writeup is
as much *negative* as positive: it quantifies "structured public data alone gets
you X% — nowhere near a sellable product."

### Path C — Targeted crawl

For a sample of companies: find each one's website, then fetch the pages likeliest
to carry a VAT number. The hidden cost is **domain discovery** — Companies House
has no website field, and mapping names to domains is its own unsolved problem
with its own false-match risk. Confront that honestly; it's a big finding.

### Recommended mix

> **Run B and C for real on a sample of ~300–500 companies. Run A as a bounded,
> measured experiment on a few CC segments. Reason about A at full scale for
> Part 3.**

Gives you: a working end-to-end pipeline (Part 2), several documented dead ends
with numbers (Part 1), and a cost-per-company extrapolation grounded in a real
measurement (Part 3). Write the decision down with its rationale — they grade the
choosing.

---

## 4. Verification design

**HMRC confirming a VAT number is valid does not mean it belongs to the company
you attached it to.** A number scraped off the wrong page — an accountant's
number in a footer, a supplier's number on a template invoice, a parent
company's number on a subsidiary's site — passes "is this a real VAT number?"
every single time.

Most submissions will report a false-positive rate near zero because they
measured **validity**, not **ownership**.

**The mechanism:** HMRC returns the registered name and address for the number.
That is the ground-truth signal.

```
candidate (company_X, vat_N)
  → HMRC lookup(vat_N) → (hmrc_name, hmrc_address)
  → agreement test: hmrc_name ≈ companies_house_name(company_X)?
                    hmrc_address ≈ companies_house_address(company_X)?
  → ACCEPT only on agreement; else REJECT and log the disagreement
```

Then be honest about the residual: name-matching is fuzzy ("J Smith Building
Services Ltd" vs "J. SMITH BUILDING SVCS LIMITED" — the customer's exact pain),
trading names differ from registered names, and registered addresses are
frequently the accountant's office and will collide (`companies_house.py
--profile` measures this). **Quantify the matcher's own error rate** on a
hand-labelled set of ~50 disagreements. That is 45 minutes and it is the most
defensible number in the submission.

**Report three numbers, not one:**

1. **Coverage** — found / sample, *and* found / estimated-VAT-registered-in-sample.
2. **Precision** — manually confirmed ownership on a hand-audited subsample of
   ~50. State sample size and confidence interval. `47/50` is honest; `94%`
   alone is not.
3. **Rejection breakdown** — how many candidates were thrown away and why. Proves
   the filter is doing work.

And state what the numbers **don't** capture — the brief asks for this and it's a
gift: selection effects, sectors never touched, the fact that false negatives are
unmeasurable here.

---

## 5. Sampling design — decide before collecting anything

The brief pre-empts the obvious cheat: *"a sample of companies you already knew
published their VAT number will produce an impressive number and teach neither of
us anything."*

**Draw the sample first, freeze it, commit it with the RNG seed, never touch it
again.** Any company added later is contamination.

- Start from the Companies House snapshot, live companies only.
- Filter to something resembling the customer: a mid-sized manufacturer's UK
  supplier base — trades, business services, logistics, wholesale, manufacturing.
  Not a random draw from the whole register (the brief says ~4.2M; measured
  5,695,465). Say so and encode it.
- **Stratify** on the axes that plausibly drive VAT-registration probability and
  web presence: SIC sector, accounts category (size proxy), company age, region.
- Seeded random draw within strata. `n ≈ 300–500`. Bigger n buys nothing; a
  *defensible* n buys everything.
- Keep a **held-out slice** (~50) untouched until the end, to check the
  extraction rules weren't overfitted to the companies you stared at.

Document: how drawn, why representative, and **in what ways it is *not*
representative.** Naming your own sample's weaknesses before they do is the
cheapest credibility win available.

---

## 6. Schedule (17–24 Aug, ~2.5 h/day)

**Day 1 (Sun 17) — Recon & unblock.** ✅ mostly done, see `CLAUDE.md`.
HMRC developer account; one successful lookup saved as raw JSON; Companies House
snapshot downloaded and queryable; repo + `DECISIONS.md` started.
*Exit: can verify a VRN programmatically; the full register in a queryable file
(measured: 5,695,465 rows).*

**Day 2 (Mon 18) — Denominator + sample.**
Estimate how many UK companies plausibly hold a VAT number — reason from the £90k
threshold, dormancy, accounts category, sector, age. Write the assumptions and the
resulting range. Budget an hour of this *away from the keyboard*. Then design and
draw the stratified sample; freeze it; commit with the seed.
*Exit: `sample.csv` committed; denominator estimate written with assumptions.*

**Day 3 (Tue 19) — Structured sources (Path B).**
Test 3–4 structured sources properly, not shallowly. Confirm or refute the
council-spend hypothesis: find the statutory field list, then check ~15 councils'
actual files. Test the EORI relationship end to end. Write up every dead end
*today*, with source, expectation, and exact reason it failed.
*Exit: 3+ documented dead ends with evidence.*

**Day 4 (Wed 20) — Targeted crawl (Path C).**
Domain discovery for the sample — measure what fraction you can find a website
for at all, and the error rate on that mapping. **That is a finding in itself.**
Fetch high-probability pages (homepage, `/contact`, `/terms`, `/privacy`,
`/about`, footer links, linked PDFs). Respect `robots.txt`, rate-limit, set a
real User-Agent with contact info — and mention that you did.
*Exit: candidate pairs with page-level provenance for every one.*

**Day 5 (Thu 21) — Common Crawl bounded experiment (Path A).**
Pull 2–4 random WET segments. Measure hits per GB, unique valid numbers, share
where you can name the owner. Extrapolate to the full crawl with stated
assumptions; compute rough cost per company acquired. **If it goes badly, that's
still a result** — write down why and what you'd need instead.
*Exit: "X VAT numbers per GB; full crawl implies ~Y at ~£Z" with arithmetic shown.*

**Day 6 (Fri 22) — Verify, measure, audit.**
Run every candidate through HMRC; store raw responses + consultation numbers. Run
the name/address agreement filter, logging every rejection with its reason.
**Hand-audit 50 accepted pairs.** Compute coverage, precision, rejection
breakdown. Run the held-out slice. Write Part 2.
*Exit: three numbers, each traceable to a file.*

**Day 7 (Sat 23) — Part 3, debate topics, polish.**
Cost per company, what breaks first, production monitoring with concrete metrics
and alert thresholds. Debate topics. Beyond-the-UK — **one country, properly, or
skip it.** Clean repo; verify everything runs from a clean clone.

**Sun 24 — Buffer, reread as a hostile reviewer, submit.**
Every number: can you point at the file that produced it? Every claim: traceable
to something you ran?

---

## 7. Traps

1. **Reporting validity as if it were ownership.** §4. The biggest one.
2. **A convenience sample.** Any company added because you knew it published its
   number invalidates the whole result.
3. **Round numbers.** "~95% precision" reads as invented. `47/50, 95% CI
   [85%, 99%]` reads as measured.
4. **Deleting the failures.** They're the deliverable.
5. **Spending 15 of 18 hours on the scraper.** The scraper is worth ~7.
6. **Vague Part 3.** "I'd use a distributed crawler" was quoted as the
   anti-example. Costs, thresholds, and a named first-thing-to-break, or don't
   bother.
7. **Not being able to reproduce your own number live in the call.**
8. **Uniform depth across sources.** Real research is lumpy — some sources get 20
   minutes, one gets four hours. Let the writeup show that.

---

## 8. Debate topics — angles, not answers

These go in `README.md`. A borrowed answer collapses under one follow-up question
in the technical call.

**1. Nine digits with a checksum — point that at HMRC's checker?**
How many candidates actually survive the checksum (measured: ~1 in 49, not 1 in
97)? How long does that take at any realistic request rate? Then the part that
matters: *what does enumeration give you even if it worked?* Valid numbers with
names attached — is that the dataset you were asked for, or a different one?
Separately: what does it look like from HMRC's side, what do their terms of use
say, and would Veridion ship a product built this way? There is a technical answer
and an ethical answer and they are not the same. Give both.

**2. Keeping it current.** What's cheap to re-check vs. what needs rediscovery?
Does Companies House's monthly delta give a trigger for *some* changes? What does
a deregistered-but-still-in-your-dataset number cost the customer? Re-verify
everything on a cycle, or sample-and-alarm?

**3. Knowing you're wrong at scale with nothing to compare against.** Internal
consistency checks, agreement between independent sources, distributional
monitoring (does the sector/region/age mix of your finds suddenly shift?),
planted canaries, sampling for human audit. Work out what you'd actually alert on.

**4. Which sources wouldn't you ship?** Be genuinely willing to name one and lose
the coverage. ToS violations, scraped aggregators, anything requiring
authenticated access or circumventing rate limits, and sources where *provenance*
can't be shown to a customer. Naming one and explaining what you'd give up beats
"all of them are fine".

---

## 9. Beyond the UK — only with time to spare

*"One country compared properly against the UK tells us more than a table."*
Obey that literally.

Germany is the suggested comparison. The interesting structural question: some
European countries **publish the VAT number in the company registry itself** — in
those, discovery isn't a problem, it's a download. Find one. Then the insight
writes itself: market difficulty isn't uniform, so you'd prioritise by
(customer demand × acquisition cost), and acquisition cost varies by orders of
magnitude. Also: VIES behaves differently per member state — some return the
trader name, some return nothing — which changes whether the *verification* step
survives the move at all.

If short on time, **skip this rather than do it badly.** A thin survey hurts.
