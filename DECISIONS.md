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
## 2026-08-18 19:00 — Check digit as a free pre-filter

**Context.** Every scraped candidate costs a verification. The check digit is free.
**Options.** Accept both allocation schemes (mod-97 and mod-9755), or pick one.
**Decision.** At the checksum stage, letting junk through costs me a manual
lookup. Binning a real number costs me a real VAT number, which nothing catches.
So I chose to be more generous with the junk. At the last filter this reverses,
because there's no one downstream, so there I'll be stricter. Putting the cheap
filter first is only safe because it can't make the undoable mistake.
**Evidence.** `python src/checksum.py` → 4,069/200,000 = 2.0345%, ~1 in 49.
**Revisit if.** [PICK ONE] (a) the checks became free and unlimited, so letting
junk through would cost nothing — or (b) candidate volume grew so large that the
manual lookups became the bottleneck, in which case a stricter filter would be
worth losing some real numbers for.


## 2026-08-18 19:30 — HMRC's VAT checker is not open

**Context.** Wanted to know whether the VAT API needs credentials at all.
**Evidence.** GET /check-vat-number/lookup/220430231 with no bearer token →
HTTP 401, body `MISSING_CREDENTIALS | Authentication information is not
provided`. Raw: `data/raw/hmrc/sandbox/220430231.noauth.single.json`.
**Decision.** The VAT API becomes useless to us since we do not have enough
time, so instead of running searches in bulk, we do them manually by using the
VAT web interface, which will take longer, and also make use of the EORI API
which doesn't have a waiting time.
**Because.** [PICK ONE] (a) the docs describe intent, but a response proves
behaviour on a date and leaves a file I can show — or (b) the specific code
mattered: 401, not 403 or 404, so I knew it was authentication and not
permissions or a missing record.

## 2026-08-18 20:00 — VAT checker gated, EORI checker open

**Context.** Needed a verifier I could actually reach.
**Evidence.** HMRC lists Check an EORI Number under "APIs with only open access
endpoints"; its spec says `AUTHORIZATIONS: None`. The VAT checker is
application-restricted. GB EORI = GB + 9-digit VRN + 000.
**Decision.** EORI only checks validity — whether the number is real. Whether it
belongs to that company depends on the trader choosing to share their name. So
EORI is my automated check that a number exists, and the VAT web form is my
manual check for whose it is.
**Because.** The VAT number sits inside the EORI number, so an open door lets me
check VAT numbers without waiting two weeks for the locked one.

## 2026-08-19 01:00 — The EORI relationship holds

**Context.** The GB EORI = GB + VRN + 000 relationship was docs, not observation.
**Evidence.** `python src/eori_client.py 220430231` → GB220430231000,
`valid: true`, from production, no authentication. No `companyDetails` block.
**Decision.** Use it as a confirming signal: valid promotes a candidate, but a
false doesn't reject one, since only businesses moving goods across a border
have an EORI at all.
**Doesn't tell me.** EORI returned valid with no name, so I am not aware of the
ownership. That comes from the VAT checker, and the web form version is
available to me, so from that I can also learn whose it is.

## 2026-08-19 01:15 — Production VAT access won't arrive in time

**Context.** Planned to verify in bulk against HMRC's API.
**Evidence.** HMRC: "Registering should take around 2 weeks. It may take longer
if we need more information." Applied 18 Aug 2026. Deadline 24 Aug 2026.
**Decision.** Verify existence automatically through EORI, and ownership by hand
through the web form on a subset.
**Because.** In the five days I have left I can check the validity of a lot of
them, and for a very few of them, whether they actually correspond to the right
company. So I will claim numbers confirmed to exist, and I will not claim a
bulk ownership-verified dataset.

## 2026-08-20 01:00 — Sample frame and stratification

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

## 2026-08-19 21:30 — Cache key collision destroyed evidence

**Context.** CLAUDE.md claimed both the 401 and 404 raw responses were on disk.
**Evidence.** Only the 404 existed — the cache keyed on VRN alone, so the
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