# How the domain-discovery data was actually collected

Factual record of the run that produced `data/results/sample_domains.csv` and
`notes/domain_discovery_output.txt`. Kept separate from the generated report
because the report gets overwritten every time it is regenerated.

## Parameters

- Input: `data/sample/sample.csv` (500 companies, seed 20260820, frozen)
- Run 19–20 Aug 2026
- 2,785 candidate domains, 5.6 per company
- Per-host delay 2.0s, global floor 0.3s, timeout 15s, 16 DNS threads
- User-Agent from `src/config.py`, carrying a contact address
- robots.txt fetched and obeyed per host; 5 candidates were skipped as disallowed
- Every attempt appended to `data/raw/domain_attempts.jsonl`

Of the 2,785 unique attempts, 2,335 (83.8%) were `DNSFailure` and never resulted
in a connection to anybody's server. 299 returned HTTP 200. The rest were TLS
failures, timeouts, and non-200 statuses.

These figures are regenerated from the log, so they move if more candidates are
attempted later; the per-company outcomes (39 strong / 125 weak / 41 none /
295 no_domain) have not changed.

## A politeness caveat, stated because it is true

Two processes ran concurrently for part of the run, and each had its own pacing
clock. So during that window a host could have received requests closer together
than the configured 2.0s per-host delay.

How it happened: the first full run was started as a background task and then
stopped after it became clear it would take hours. `TaskStop` killed the shell
wrapper — its output file ends with `[killed]` — but the `nohup python` child
survived it and ran to completion, writing 675 lines of progress and its own copy
of the output files. Meanwhile the replacement run was already going. Both wrote
to the same append-only attempt log, which is why 2,531 keys appear in it twice.
The file currently holds 5,316 lines for 2,785 unique attempts.

Consequences, in order of how much they matter:

1. **The pacing guarantee was broken for that window.** Two clocks, no
   coordination. The absolute volume was low — at most two requests per host from
   each process, against mostly distinct hosts — but the claim "no host was
   contacted faster than once every 2 seconds" cannot be made for this run.
   It holds for any single-process run, which is what the code now does.
2. **The data is unaffected.** The attempt log is append-only and loaded into a
   dict keyed on `(company_number, candidate_domain)`, so duplicates collapse and
   the last write wins. Both processes ran identical scoring logic against the
   same pages. The outcome counts regenerate identically from the cache:
   39 strong / 125 weak / 41 none / 295 no_domain.
3. **The live fetch counters were lost.** They existed only in process memory,
   and a later `--report-only` run overwrote the report with zeroes. Fixed: the
   attempt breakdown in the report is now derived from the attempt log on disk,
   so it regenerates identically instead of depending on which process last
   exited.

Two changes came out of this:

- Attempt records now carry `attempted_at`. Records written before that change
  do not have it, which is why the double-written keys cannot be attributed to
  one run or the other after the fact.
- Anything reported from a run has to be recomputable from files on disk. A
  number that lives in a process counter is not evidence, because the first time
  you regenerate the report it becomes a zero.

## Reproducing

The attempt log makes the whole run replayable with no network access:

```bash
python src/domain_discovery.py --report-only
```

That rebuilds both the CSV and the report from `data/raw/domain_attempts.jsonl`.
A fresh network run against the same frozen sample would re-fetch only candidates
absent from the log.
