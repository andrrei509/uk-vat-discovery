"""
Write the 20 sampled domain-discovery rows to a sheet for hand-checking.

Same 20 rows the report prints, in the same seeded order, but as a CSV with
blank columns to fill in. The report is for reading; this is for working
through with a browser open.

Filling this in is what turns the strong/weak/none split from a machine's
opinion into a measured error rate: `match_strength` is what the matcher
thought, `verdict` is what is actually true, and the disagreement between them
is the number.

Refuses to clobber work
-----------------------
If the sheet already has a filled row, existing rows are preserved exactly and
in place, and only genuinely new ones are appended. Hand-entered data is the
most expensive data in the repo — the same rule `src/audit_worklist.py` follows.

Usage
-----
    python src/domain_audit_sheet.py
    python src/domain_audit_sheet.py --seed 20260820 --n 20
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from domain_discovery import (  # noqa: E402
    STRENGTH_ORDER, load_sample, SEED, N_HANDCHECK,
)

RESULTS = REPO_ROOT / "data" / "results" / "sample_domains.csv"
OUT = REPO_ROOT / "audit" / "domain_audit.csv"

PREFILLED = ["company_number", "company_name", "ch_address", "sector_group",
             "size_band", "candidate_domain", "match_strength", "matched_on",
             "notes"]
MANUAL = ["verdict", "what_the_site_actually_is", "checked_at"]
COLUMNS = PREFILLED + MANUAL
VERDICTS = ("correct", "wrong", "unclear")

KEY = ("company_number", "candidate_domain")


def read_sheet(path: Path) -> tuple[list[str], list[dict]]:
    comments: list[str] = []
    lines = path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    body = 0
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            comments.append(line.rstrip("\r\n"))
            body = i + 1
        else:
            break
    return comments, [dict(r) for r in csv.DictReader(lines[body:])]


def is_filled(row: dict) -> bool:
    return any((row.get(c) or "").strip() for c in MANUAL)


def load_results() -> list[dict]:
    lines = [ln for ln in RESULTS.read_text(encoding="utf-8-sig").splitlines(keepends=True)
             if not ln.lstrip().startswith("#")]
    return [dict(r) for r in csv.DictReader(lines)]


def pick(rows: list[dict], seed: int, n: int) -> list[dict]:
    """
    The same selection the report makes: spread across every outcome present,
    seeded, so the sheet and the report never disagree about which 20 rows.
    """
    import numpy as np

    counts = {s: sum(1 for r in rows if r["match_strength"] == s) for s in STRENGTH_ORDER}
    present = [s for s in STRENGTH_ORDER if counts[s] > 0]
    pools = {s: sorted([r for r in rows if r["match_strength"] == s],
                       key=lambda r: r["company_number"]) for s in present}

    per = {s: 0 for s in present}
    remaining = n
    while remaining > 0:
        takers = [s for s in present if per[s] < len(pools[s])]
        if not takers:
            break
        for s in takers:
            if remaining == 0:
                break
            per[s] += 1
            remaining -= 1

    rng = np.random.default_rng(seed)
    picked = []
    for s in present:
        pool, k = pools[s], per[s]
        if k:
            idx = rng.choice(len(pool), size=k, replace=False)
            picked.extend(pool[i] for i in sorted(int(i) for i in idx))
    return picked


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the domain hand-check sheet")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--n", type=int, default=N_HANDCHECK)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not RESULTS.exists():
        raise SystemExit(f"no {RESULTS}; run python src/domain_discovery.py first")

    results = load_results()
    sample = {r["company_number"]: r for r in load_sample()}
    picked = pick(results, args.seed, args.n)

    fresh = []
    for r in picked:
        s = sample.get(r["company_number"], {})
        addr = ", ".join(p for p in (s.get("reg_address_line1"),
                                     s.get("reg_post_town"),
                                     s.get("reg_postcode")) if p)
        row = {c: "" for c in COLUMNS}
        row.update({
            "company_number": r["company_number"],
            "company_name": r["company_name"],
            "ch_address": addr,
            "sector_group": s.get("sector_group", ""),
            "size_band": s.get("size_band", ""),
            "candidate_domain": r["candidate_domain"],
            "match_strength": r["match_strength"],
            "matched_on": r["matched_on"],
            "notes": r["notes"],
        })
        fresh.append(row)
    # Deterministic file order, independent of the seeded draw order.
    fresh.sort(key=lambda r: (STRENGTH_ORDER.index(r["match_strength"]),
                              r["company_number"]))

    comments, existing = ([], [])
    if args.out.exists():
        comments, existing = read_sheet(args.out)
    filled = sum(1 for r in existing if is_filled(r))
    have = {tuple(r.get(c, "") for c in KEY) for r in existing}
    new = [r for r in fresh if tuple(r[c] for c in KEY) not in have]

    print(f"results      : {len(results)} rows in {RESULTS.name}")
    print(f"selected     : {len(fresh)} (seed {args.seed}, spread across outcomes)")
    print(f"existing     : {len(existing)} row(s), {filled} filled in")
    print(f"new to add   : {len(new)}")
    for s in STRENGTH_ORDER:
        k = sum(1 for r in fresh if r["match_strength"] == s)
        if k:
            print(f"  {s:<10} {k}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0
    if existing and not new:
        print("\nnothing new; sheet left untouched")
        return 0

    if filled:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = args.out.with_name(f"{args.out.stem}.backup-{stamp}{args.out.suffix}")
        shutil.copy2(args.out, backup)
        print(f"\nbacked up {filled} filled row(s) -> {backup.name}")

    if not comments:
        comments = [
            "# Hand-check of src/domain_discovery.py. Fill in verdict,",
            f"# what_the_site_actually_is and checked_at. verdict: {' | '.join(VERDICTS)}",
            "# 'correct' = the domain really is this company's. 'wrong' = it is",
            "# somebody else's, parked, or unrelated. match_strength is what the",
            "# matcher decided; verdict is what is true.",
            f"# selection: seed {args.seed}, spread across outcomes, regenerate with",
            "#   python src/domain_audit_sheet.py",
        ]

    rows = existing + new
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(args.out.suffix + ".tmp")
    # utf-8-sig: this gets opened in Excel and typed into.
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        for c in comments:
            fh.write(c + "\n")
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") or "" for c in COLUMNS})
    tmp.replace(args.out)
    print(f"wrote {args.out} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
