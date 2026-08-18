# Reference data

Small, committed inputs. Not results.

## `hmrc_sandbox_test_vrns.csv`

The 40 mock VAT numbers HMRC publishes for sandbox testing.
Source: <https://github.com/hmrc/vat-registered-companies-api/tree/main/public/api/conf/2.0/test-data>
Mirrored 18 Aug 2026 so the repo is self-contained.

## `probe_domains.csv`

**A probe set. NOT the sample.**

Purpose: produce real VAT numbers quickly, so the EORI checker's trader-detail
opt-in rate can be measured. That rate is a property of HMRC's service, so a
convenience sample is acceptable for measuring it.

It is **not** acceptable for measuring coverage, and the brief says so directly:

> "a sample of companies you already knew published their VAT number will
> produce an impressive number and teach neither of us anything"

Rules:
- Never merge this into `data/sample/sample.csv`.
- Outputs go to `data/results/probe_*.csv`, never `candidates.csv`.
- Any number reported from this set must be labelled as coming from the probe.

Fill it in yourself: `company,domain`, one per line, ~30 rows. Pick UK
businesses that plausibly move goods across a border — importers, wholesalers,
manufacturers, online retailers — because only EORI holders can answer the
opt-in question at all. Write down in `DECISIONS.md` how you picked them and
what that biases.
