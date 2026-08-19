# Numeric claims with no reproducing command

Audit run 20 Aug 2026 across `README.md`, `DECISIONS.md`, `CLAUDE.md`,
`docs/PLAN.md`, `notes/` and the `src/` docstrings. Nothing has been deleted and
no command has been invented for anything.

Claims that **do** have a command are in the traceability table in `README.md`
under *How to reproduce*. Every one was re-run during the audit and reproduced
its stated value exactly.

**Status: 6 of the original 11 findings are fixed. What remains is below.**

---

## RESOLVED 20 Aug

| # | Was | Now |
|---|---|---|
| A1 | CSV size given as both `~2.5 GB` and `2.8 GB` in one file | `src/companies_house.py` states the measured bytes for both the zip and the extract, with `--hash` as the command |
| A2 | row count `~5.4M` vs measured `5,695,465` | `src/companies_house.py` and `docs/PLAN.md` state the measured count and note that the older figure is unsupported |
| A3 | `~4.2M` live companies presented as fact | `docs/PLAN.md` labels it as *the brief's* figure and puts the measured `5,695,465` live / `5,190,464` Active beside it |
| A4 | `1 in 97` stated without saying it is the single-scheme rate | `src/checksum.py` states both: ~1 in 97 for one scheme, measured `4069/200000 = 2.0345%` (~1 in 49) accepting both |
| B1 | repo claimed a 401 response it did not have | the cache key is now `(vrn, env, auth-mode, endpoint-variant)`, a `--no-auth` flag captures the 401 deliberately, and the real body is committed at `data/raw/hmrc/sandbox/220430231.noauth.single.json` |
| E | `47/50` readable as a result | `README.md` marks it explicitly as a format example to be replaced |

On B1, the root cause is worth keeping written down: `_cache_path()` keyed on the
VRN alone, so two genuinely different responses for the same number shared one
filename and the later authenticated 404 overwrote the earlier unauthenticated
401. The fix also has a compatibility path for old `{vrn}.json` files, which
infers their mode from their status code (401 implies no token was sent) rather
than serving them for any mode — serving them unconditionally reintroduced the
same class of bug from the other direction, which happened once during the fix
and is why the check exists.

---

## STILL OPEN

### B2. "6 days away"

`CLAUDE.md` — date arithmetic from 18 Aug to the 24 Aug deadline. No command, and
it goes stale daily. Either make it an absolute date or drop it.

### C. Externally sourced — cited, but not reproducible from this repo

Each has a source, which is not the same as having a command. None is mirrored
into the repo, so a reader cannot check them without leaving it.

| Claim | Where | Source |
|---|---|---|
| `2.73M` VAT and/or PAYE registered enterprises, of which `2.10M` companies | `CLAUDE.md` | ONS *UK Business 2025*, 14 Mar 2025 |
| `~2.18M` VAT registrations nationally | `docs/PLAN.md` | the assignment brief |
| `~4.2M` live companies | `docs/PLAN.md` | the assignment brief — now labelled as theirs, with the measured value beside it (A3), but still not reproducible from here |
| production access takes `around 2 weeks` | `notes/hmrc_api_findings.md:29` | HMRC Developer Hub, quoted verbatim |
| Common Crawl is `~100 TB`; WET files `~150 MB` gzipped | `docs/PLAN.md` | Common Crawl docs |
| Local Government Transparency Code **2015** field list | `docs/PLAN.md` | the statute; PLAN.md already flags it "confirm yourself" |

The HMRC quote is a verbatim citation rather than a computed number, so "no
command" is arguably the wrong test for it. Listed for completeness.

### D. Trivially derivable, but no command is written down

| Claim | Where | Note |
|---|---|---|
| `22` are 9-digit, `18` are 12-digit | `notes/hmrc_api_findings.md:70` | Verified correct by counting the mirrored CSV. The neighbouring 1-of-40 claim has a one-liner at `:77`; this one does not. |
| `40` mock VRNs | `notes/hmrc_api_findings.md:68`, `data/reference/README.md:7` | Row count of the mirrored CSV. |
| `~470 MB` zip | `data/companies_house/README.md:3` | Measured 493,049,031 bytes = 0.49 GB decimal / 0.46 GiB, so "~470 MB" holds only if MB means MiB. `src/companies_house.py` now states the exact bytes; this file still rounds. |
| `~1 in 49` | `CLAUDE.md`, `src/checksum.py` | Derived from the measured 2.0345%; no command prints the reciprocal. |

### F. Unmeasured performance claim

| Claim | Where |
|---|---|
| parquet is `~10x faster` than the CSV | `data/companies_house/README.md:17`, `src/companies_house.py` |

No benchmark is committed. Observed but not recorded by any script: 15s for
`--to-parquet`, 2.8s for `--profile` against the parquet. No CSV-path timing was
taken, so the 10x is unsupported in either direction.

### G. Illustrative, not claims

| Value | Where | What it is |
|---|---|---|
| `47/50 → [0.837829, 0.979385]` | `src/metrics.py` self-test | A unit-test fixture for the Wilson interval, not a result about VAT numbers. |
| `~300–500` sample, `~50` held out, `~15` councils, `~30` rows | `docs/PLAN.md`, `data/reference/README.md` | Targets and plans, not measurements. |

These are fine as they stand. Listed so a later reader does not mistake them for
findings.
