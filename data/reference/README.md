# Reference data

Small, committed inputs. Not results.

## `hmrc_sandbox_test_vrns.csv`

The 40 mock VAT numbers HMRC publishes for sandbox testing.
Source: <https://github.com/hmrc/vat-registered-companies-api/tree/main/public/api/conf/2.0/test-data>
Mirrored 18 Aug 2026 so the repo is self-contained.

## ~~`probe_domains.csv`~~ — considered, then dropped

**The file is gone. The reasoning is kept, because it is why the sample exists.**

The plan was a hand-picked probe set: UK businesses that plausibly move goods
across a border — importers, wholesalers, manufacturers, online retailers —
crawled to produce real VAT numbers quickly, so the EORI checker's trader-detail
opt-in rate could be measured. That rate is a property of HMRC's service rather
than of who you point at it, so a convenience sample is acceptable *for that one
measurement*.

It is **not** acceptable for measuring coverage, and the brief says so directly:

> "a sample of companies you already knew published their VAT number will
> produce an impressive number and teach neither of us anything"

That constraint is the whole reason the probe was kept separate from the sample,
under its own rules:

- never merged into `data/sample/sample.csv`
- outputs to `data/results/probe_*.csv`, never `candidates.csv`
- any number from it labelled as coming from the probe

**Why it was dropped.** The template was never filled in, and once
`data/sample/sample.csv` was drawn and frozen the probe had nothing left to do:
the frozen sample produces real VAT numbers *and* is defensible for coverage, so
running a convenience set alongside it would have added a second, weaker source
of numbers that every reported figure would then have to be qualified against.
The opt-in rate is now measured from candidates found on the sample's own
domains instead — see `data/raw/eori/production/`.

The constraint above still binds anything that replaces it. If a probe set is
ever reintroduced, it comes back with these rules attached.
