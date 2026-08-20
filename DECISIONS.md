# Decision log

Dated entries, written *as I go*. What I decided, what I rejected, why, and
what I'd revisit with more time.

Format for each entry:

```
## YYYY-MM-DD HH:MM — <one-line title>

**Context.**   What I was trying to do.
**Options.**   What I considered.
**Decision.**  What I chose.
**Because.**   The reason, including what I'm giving up.
**Evidence.**  File / command / number that backs this up (if any).
**Revisit if.** What would change my mind.
```

Not every entry needs all six lines. Entries where I was wrong stay in.

---
## 2026-08-18 19:00 Check digit as a free pre-filter

**Context.** Every scraped candidate costs a verification. The check digit is free.
**Options.** Accept both allocation schemes (mod-97 and mod-9755), or pick one.
**Decision.** At the checksum stage, letting junk through costs me a manual
lookup. Binning a real number costs me a real VAT number, which nothing catches.
So I chose to be more generous with the junk. At the last filter this reverses,
because there's no one downstream, so there I'll be stricter. Putting the cheap
filter first is only safe because it can't make the undoable mistake.
**Evidence.** `python src/checksum.py` → 4,069/200,000 = 2.0345%, ~1 in 49.
**Revisit if.** The checks became free and unlimited, so letting junk through
would cost nothing. Or if candidate volume grew so large that the manual
lookups became the bottleneck, in which case a stricter filter would be worth
losing some real numbers for.


## 2026-08-18 19:30  HMRC's VAT checker is not open

**Context.** Wanted to know whether the VAT API needs credentials at all.
**Evidence.** GET /check-vat-number/lookup/220430231 with no bearer token →
HTTP 401, body `MISSING_CREDENTIALS | Authentication information is not
provided`. Raw: `data/raw/hmrc/sandbox/220430231.noauth.single.json`.
**Decision.** The VAT API becomes useless to us since we do not have enough
time, so instead of running searches in bulk, we do them manually by using the
VAT web interface, which will take longer, and also make use of the EORI API
which doesn't have a waiting time.
**Because.** The docs describe intent, but a response proves behaviour on a
date and leaves a file I can show. And the specific code mattered: 401, not 403
or 404, so I knew it was authentication and not permissions or a missing record.

## 2026-08-18 20:00 VAT checker gated, EORI checker open

**Context.** Needed a verifier I could actually reach.
**Evidence.** HMRC lists Check an EORI Number under "APIs with only open access
endpoints"; its spec says `AUTHORIZATIONS: None`. The VAT checker is
application-restricted. GB EORI = GB + 9-digit VRN + 000.
**Decision.** EORI only checks validity, whether the number is real. Whether it
belongs to that company depends on the trader choosing to share their name. So
EORI is my automated check that a number exists, and the VAT web form is my
manual check for whose it is.
**Because.** The VAT number sits inside the EORI number, so an open door lets me
check VAT numbers without waiting two weeks for the locked one.


## 2026-08-19 06:15  Production VAT access won't arrive in time

**Context.** Planned to verify in bulk against HMRC's API.
**Evidence.** HMRC: "Registering should take around 2 weeks. It may take longer
if we need more information." Applied 18 Aug 2026. Deadline 24 Aug 2026.
**Decision.** Verify existence automatically through EORI, and ownership by hand
through the web form on a subset.
**Because.** In the five days I have left I can check the validity of a lot of
them, and for a very few of them, whether they actually correspond to the right
company. So I will claim numbers confirmed to exist, and I will not claim a
bulk ownership-verified dataset.


## 2026-08-19 21:30  Cache key collision destroyed evidence

**Context.** CLAUDE.md claimed both the 401 and 404 raw responses were on disk.
**Evidence.** Only the 404 existed, the cache keyed on VRN alone, so the
authenticated 404 overwrote the 401 five minutes later. Fixing it reintroduced
the same bug from the other direction via the legacy fallback. Full chronology
in `notes/cache_key_bug.md`.
**Decision.** I decided to capture the 401 again for real and keep both files,
instead of softening the sentence that claimed them. The cache key now includes
the environment and whether the request was authenticated, so two different
requests can't overwrite each other.
**Because.** If the claim had no file behind it, the fix was to produce the missing
file. An evidence file has to be keyed by everything that makes the response different,
otherwise one run silently destroys another and I probably wouldn't find out until someone checks.


## 2026-08-19 23:30  Sample frame and stratification

**Context.** Needed a defensible ~500 companies, frozen before any collection.
**Evidence.** Frame 2,362,322 after exclusions (waterfall in
`notes/sample_design_output.txt`). Stratified sector × size, 20 cells,
proportional, n=500, seed 20260820, 50 holdout. `larger` band got 14 companies;
business_services is 41.5% of the frame.
**Decision.** I took a proposed sector list and checked it looked like a
manufacturer's supply base, then accepted it. I also excluded dormant and
non-trading companies, because a dormant company isn't trading and so can't
meaningfully have a VAT number, thus leaving them in would push my coverage down
for a reason that teaches nothing.
**Because.** Proportional allocation means the big groups get most of the sample
and the small ones get very little, `larger` only got 14 companies, and that's
the band most likely to have a VAT number and publish it. I accept that because
proportional gives an honest picture of the frame as a whole, which is what
coverage means. What it costs me is that I can't say anything reliable about
large companies specifically.
**Revisit if.** I had more time, I'd draw a separate booster sample of `larger`
companies and report it on its own rather than merged into coverage.



## 2026-08-20 00:45  Domain discovery is the binding constraint

**Context.** Companies House has no website field. Before crawling anything I
had to find out how many of my 500 companies I could even locate a site for.
**Evidence.** src/domain_discovery.py over the frozen sample:
  strong 39 (7.8%) | weak 125 (25.0%) | none 41 (8.2%) | no_domain 295 (59.0%)
  2,785 candidate attempts, 83.8% DNSFailure, only 299 HTTP 200s.
  notes/domain_discovery_output.txt
**Decision.** Count only `strong` as found. `weak` means the page contained the
company's distinctive word, but that word is what generated the domain, so the
test is near circular.
**Because.** Counting weak would have given 32.8% instead of 7.8%, four times
larger and wrong. kraken.com scored weak for KRAKEN INC LIMITED; mapach.com is a
parked domain for sale.
**What it means.** Even a perfect extractor caps at 7.8% on this route.



## 2026-08-20 01:30  Realized auditing is not filtering

**Context.** Hand checking the 20 sampled rows in audit/domain_audit.csv to
measure how often my domain classifier is wrong.
**Evidence.** Checked the 5 strong rows first. 4 correct 1 wrong.
  - SOUTH YORKSHIRE BUILDERS: page states "Company number 15887473" and
    "4 Boland Road, Sheffield", both match Companies House exactly.
  - BSN (LONDON): page states "Registration No: 5050419", matches CH 05050419.
    Its address (4 Prince Albert Road) does NOT match CH (55 Loudoun Road), they moved.
  - ETI LOGISTICS SUPPORT and TIMGLOBAL: exact name, consistent geography, no
    company number on the page.
  - PHSC: footer says "Registered in England, No. 2485626", Personnel Health &
    Safety Consultants Ltd, a wholly-owned subsidiary. My sampled company is
    PHSC plc, 04121793. Different legal entity, same premises and brand.
**Decision.** Marked PHSC wrong. When auditing I record what is true, not what
is convenient for the pipeline, a filter applies a policy, an audit records a
fact, and shading audit verdicts destroys the measurement I was taking.
**Because.** I almost justified marking it correct on the grounds that a later
filter would catch it. That reasoning belongs to filtering, not auditing, and it
would have inflated my own accuracy figure.
**Rule I'm carrying to the HMRC check.** A company number match is decisive. An
address mismatch is a question, not a rejection, BSN proves companies move. The
trap is a page (or an HMRC response) that names a different legal entity.
**Revisit if.** I find the subsidiary/parent case is common enough to need
handling rather than rejecting.

## 2026-08-20 02:00  Two agents edited one file and silently broke it

**Context.** src/sources/website_vat.py was being changed in two sessions at once.
**Evidence.** The merged file would have raised
`Candidate.__init__() missing 1 required positional argument` on the first VAT
number found, the dataclass had gained a required company_number that
scan_domain() never passed. It also had two load_domain_rows definitions, the
second silently shadowing the first, plus a dead DomainRow dataclass.
A --limit 1 smoke test passed, because that domain happened to yield zero VRNs.
The bug was only found by constructing a Candidate directly.
All 39 strong domains would have hit it, and 6 of them did produce numbers.
**Decision.** One writer per file. Changes get handed over, not applied in
parallel.
**Because.** A passing test proved nothing here, it exercised the path where
the bug can't fire. Same shape as the cache key collision: concurrent writes,
silent damage, and only a deliberate check surfaced it.

## 2026-08-20 02:45  First end-to-end result: 6 candidate VAT numbers

**Context.** Crawled the 39 strong match domains for VAT numbers.
**Evidence.** 6 of 39 domains yielded at least one VRN (15.4%). 6 distinct
(company, vrn) pairs, all 6 found next to a VAT label, 0 unlabelled.
111 pages fetched, 435 paths returned 404, 0 blocked by robots.txt, 0 errors.
  05131762 LOVE WATER      lovewater.com          851194036  (/terms)
  06759982 ONBIKE          onbike.co.uk           943190133
  09412758 SURPLUSDRIVESUK surplusdrivesuk.co.uk  213969785
  12983899 CARCODE         carcode.uk             372282495
  13589368 ECONOSTORE      econostore.co.uk       447782355
  15532140 TERRASTRUCT     terrastruct.co.uk      503939094
**Decision.** Report this as a funnel, not as one coverage number. 6/500 on its
own says nothing useful but 500 -> 39 -> 6 says where the problem is.
**Because.** 461 companies were lost at domain discovery and 33 at extraction,
a factor of fourteen. Improving the extractor can't move the result much because
it only ever sees 39 companies. Any real improvement has to come from the domain
step, or from abandoning the site-by-site shape entirely.



## 2026-08-20 05:00  Hand audit finished, five distinct failure modes

**Context.** All 20 rows of audit/domain_audit.csv checked by hand.
**Evidence.** 11 correct, 7 wrong, 2 unclear. Strong 4/5.
  Errors split 3 false positives / 4 false negatives:
  1. PHSC - right brand, wrong legal entity (subsidiary 2485626)
  2. ODYSSEY - odyssey-electrical.co.uk, shortened trading form, never generated
  3. MPL GROUNDWORK - trades as MPL Contractors, never generated
  4. AJE TECH - right domain generated, blocked by HTTP 403
  5. DID TEACH - right domain, redirects to thosewhocan.org, matcher rejected it
  Weighted by bucket size, roughly 240 of 500 companies appear to have a
  findable website. My method reached 39.
**Decision.** 7.8% is a floor, not ceiling. My method is the limit not the data
**Because.** 3 of the 5 companies my classifier gave up on turned out to have websites when I searched by hand. So the companies
are on the web, I just cannot reach them by guessing domains from a registered name. That changes the narrative from "the data isn't out there" to "my method might be weak, and here are 5 reasons why", and it means domain discovery is worth spending money on at scale, because there is a headroom above 7.8%.
**Notice.** The audit sampled 5 per outcome, not proportionally, and the
estimate above rests on 3/5 in the no_domain bucket driving 295 companies.
Indicative only.
**Note.** MPL, DID TEACH and FISH! RESTAURANTS all trade under a name they are
not registered under. 3 of 20.

## 2026-08-20 05:30  The AI summaries I used were wrong twice in five

**Context.** Used a search engine's AI overview to hand check companies.
**Evidence.** It contradicted my own Companies House snapshot twice:
  SURIYA JEWELS - claimed 318 High St North E12 6AB; snapshot says 292, E12 6SA
  UNINHIBITED - claimed 86-90 Paul Street EC2A 4NE; snapshot says 120 Bennetts
    Close, Mitcham CR4 1NS (86-90 Paul Street holds 8,610 companies)
  MAPACH - linked to officers of company 10691545, a different company entirely
**Decision.** An AI summary is a lead, not evidence. It tells me where to look.
Only Companies House, HMRC, and pages I opened myself count as evidence, and
those are what I record.
**Because.** Two of five summaries contradicted my own Companies House
snapshot, and one linked me to a different company entirely. If I had taken
them as fact, wrong addresses would have gone into my results and nothing
downstream would have caught it.

## 2026-08-20 06:00  The weak experiment, priced

**Context.** Crawled the 125 weak domains into their own file to find out what
excluding them actually cost.
**Evidence.** weak yielded 2 VAT numbers from 125 domains (1.6%).
  strong yielded 6 from 39 (15.4%). Ten times the rate.
  Files never merged: data/results/candidates.csv, candidates_weak.csv
**Decision.** Keep the exclusion.
**Because.** 39 strong domains gave 6 VAT numbers and all six were confirmed as
belonging to the company. 125 weak domains gave 2, and neither belonged to the
company they were found on. Three times the crawling, a third of the numbers,
none of them right. The gap isn't that weak is less productive, it produced
zero correct pairs.
**But it wasn't free.** ADVANCED ROOF TESTING was classified weak and is
genuinely their site (CH address 62 The Pines, Horsham RH12 4UE matches). I
threw away at least one true positive.

## 2026-08-20 07:30  Ownership tested by hand: strong 6/6, weak 0/2

**Context.** Typed all 8 candidates into gov.uk/check-uk-vat-number by hand and
compared HMRC's returned name against Companies House. audit/manual_audit.csv
**Evidence.**
  strong 6/6 owned  - LOVE WATER, ONBIKE, SURPLUSDRIVESUK, CARCODE,
                      ECONOSTORE, TERRASTRUCT all matched
  weak   0/2 owned
    156633205 found on thinklab.co.uk, attributed to THINK-LAB LIMITED
      (04417292). HMRC returns THINKLAB (UK) LTD, 08318916.
    464381382 found on indeliblefineart.co.uk, attributed to INDELIBLE FINE ART
      LTD (17250681). HMRC returns INDELIBLE STUDIOS LIMITED, 15260721,
      Brighton BN1 1UT.
  Precision 6/6, 95% CI [0.610, 1.000]. Including weak: 6/8 = 0.750.
**The case that matters.** 464381382 is a real, valid, HMRC confirmed VAT
number. It's not this company's. It came from the weak bucket I excluded, and
every automated check in my pipeline (checksum, EORI, format) would have
accepted it. Only the name comparison caught it.
  Evidence: data/raw/eori/production/GB464381382000.json,
  data/results/candidates_weak.csv, Companies House 15260721.
**Also confirmed.** ECONOSTORE and TERRASTRUCT returned invalid from EORI, and
HMRC says both numbers are real and theirs. An EORI false means the company
has no EORI, not that the number is fake. If I had used EORI as a filter I would
have discarded two correct pairs.
**Decision.** What I claim: 6 pairs of the type (company, VAT number) where HMRC's
registered name matches the company, each checked by hand, one at a time, with
the response recorded.

What my numbers DON'T show:
 - 6/6 is not proof of perfect accuracy. 6 pairs is too few. The 95%
   interval runs from 0.61 to 1.00, and my domain audit says roughly 1 in 5
   strong domains is wrong, so across 6 pairs I should have expected about 1
   bad one. Getting none is luck, not proof.
 - The 1.2% coverage applies to my sample, not to UK companies in general. I
   excluded dormant and non trading companies, and business services are 41.5%
   of my frame.
 - A company where I found nothing may simply not be VAT registered. I can't
   tell those two apart.
 - The ownership check does not scale. It's me typing into a web form, 1
   number at a time.
