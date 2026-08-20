"""
Read the filled manual-audit sheet and compute the three numbers.

Reporting rules this module enforces
------------------------------------
1. **Every number is printed with the count it came from.** `47/50 = 0.940`,
   never a bare `94%`. A round percentage reads as invented; a fraction shows
   its own sample size and lets a reader do their own arithmetic.

2. **Fractions, not percentages.** Deliberate: a percentage on n=50 invites the
   reader to treat it as precise to the point. `0.940` next to `47/50` does not.

3. **Wilson score interval, not the normal approximation.** n here is ~50 and
   the proportion sits near 1.0, which is exactly where the normal
   approximation breaks — it happily returns upper bounds above 1.0 and its
   coverage is poor in the tail. Wilson stays inside [0, 1] and behaves at the
   boundary.

4. **Nothing is inferred to fill a gap.** If the sample denominator was not
   supplied, coverage prints as unavailable rather than guessing at one. A
   missing number is a visible gap; an invented denominator silently corrupts
   every number derived from it.

What "found" means is Andrei's call, not this script's, so both plausible
numerators are reported side by side and neither is labelled "the" coverage.

Usage
-----
    python src/metrics.py
    python src/metrics.py --sheet audit/manual_audit.csv --sample data/sample/sample.csv
    python src/metrics.py --candidates data/results/candidates_raw.csv
    python src/metrics.py --self-test
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = REPO_ROOT / "audit" / "manual_audit.csv"
DEFAULT_SAMPLE = REPO_ROOT / "data" / "sample" / "sample.csv"

Z95 = 1.959963984540054  # two-sided 95%


# --------------------------------------------------------------------------
# Statistics


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """
    Wilson score interval for k successes in n trials.

        centre = (p + z²/2n) / (1 + z²/n)
        half   = z/(1 + z²/n) · sqrt( p(1-p)/n + z²/4n² )

    Chosen over the normal approximation because n is small and p sits near the
    boundary. At 50/50 the normal approximation gives an upper bound of 1.0
    exactly and a lower bound that is far too high; Wilson gives an honest
    interval that never leaves [0, 1].
    """
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = (z / d) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, centre - half), min(1.0, centre + half))


def frac(k: int, n: int) -> str:
    """`47/50 = 0.940` — the count is never separated from the ratio."""
    if n <= 0:
        return f"{k}/0 = n/a (no denominator)"
    return f"{k}/{n} = {k / n:.3f}"


# --------------------------------------------------------------------------
# Loading


def load_sheet(path: Path) -> list[dict]:
    from audit_worklist import read_sheet

    _, rows = read_sheet(path)
    return rows


def load_column(path: Path, *names: str) -> list[str]:
    """
    First matching column from a CSV, as a list of stripped strings.

    Leading `#` lines are skipped. Both `data/sample/sample.csv` and
    `audit/manual_audit.csv` carry a provenance header block, and without this
    the first comment line would be parsed as the header row — giving a
    "column not present" error on a file that plainly contains the column.
    """
    with path.open(encoding="utf-8-sig", newline="") as fh:
        lines = [ln for ln in fh.read().splitlines(keepends=True)
                 if not ln.lstrip().startswith("#")]
        rows = [dict(r) for r in csv.DictReader(lines)]
    if not rows:
        return []
    for n in names:
        if n in rows[0]:
            return [(r.get(n) or "").strip() for r in rows]
    raise SystemExit(f"{path.name}: none of these columns present: {names}\n"
                     f"found: {', '.join(rows[0].keys())}")


# The audit sheet is filled in by hand, and the hand-built version does not use
# the same column names as the generated one. Both are correct; the reader
# adapts rather than the sheet being renamed to suit the reader. First name
# present on the row wins.
ALIASES = {
    "company_name":  ("company_name", "ch_name"),
    "checked_at":    ("checked_at", "hmrc_checked_at"),
    "source":        ("domain", "source_url"),
    "ch_postcode":   ("ch_postcode",),
    "ch_address":    ("ch_address", "ch_address_line1"),
    "hmrc_address":  ("hmrc_address",),
    "hmrc_name":     ("hmrc_name",),
    "hmrc_postcode": ("hmrc_postcode",),
}

# `not_registered` is a distinct outcome from `not_owns`: the number is not on
# the register at all, rather than belonging to somebody else. Both are failures
# of the candidate, so both sit in the precision denominator, but they are
# counted separately because they point at different upstream problems.
VERDICTS = ("owns", "not_owns", "ambiguous", "not_registered")


def field(row: dict, key: str) -> str:
    for name in ALIASES.get(key, (key,)):
        v = (row.get(name) or "").strip()
        if v:
            return v
    return ""


def norm_cn(v: str) -> str:
    v = (v or "").strip().upper()
    return v.zfill(8) if v.isdigit() else v


# --------------------------------------------------------------------------
# Report


def report(sheet: Path, sample: Optional[Path], candidates: Optional[Path]) -> int:
    rows = load_sheet(sheet)
    print(f"sheet     : {sheet}")
    print(f"sheet rows: {len(rows)}")
    if not rows:
        print("\nsheet is empty - nothing to compute.")
        return 0

    audited = [r for r in rows if (r.get("verdict") or "").strip()]
    verdicts: dict[str, list[dict]] = {}
    for r in audited:
        verdicts.setdefault(r["verdict"].strip().lower(), []).append(r)

    unknown = {k: v for k, v in verdicts.items() if k not in VERDICTS}
    owns = verdicts.get("owns", [])
    not_owns = verdicts.get("not_owns", [])
    ambiguous = verdicts.get("ambiguous", [])
    not_registered = verdicts.get("not_registered", [])

    companies = {norm_cn(r.get("company_number", "")) for r in rows if r.get("company_number")}
    owns_companies = {norm_cn(r["company_number"]) for r in owns if r.get("company_number")}

    # ---------------------------------------------------------------- coverage
    print("\n=== coverage " + "=" * 52)
    if sample is None:
        print("  sample denominator not supplied (--sample) -> coverage UNAVAILABLE.")
        print("  Not inferred: an invented denominator would corrupt every number below it.")
        n_sample = 0
    else:
        sample_ids = {norm_cn(v) for v in load_column(sample, "company_number", "CompanyNumber") if v}
        n_sample = len(sample_ids)
        print(f"  sample file            : {sample}")
        print(f"  sample size            : {n_sample} distinct compan{'y' if n_sample == 1 else 'ies'}")
        in_sample = companies & sample_ids
        outside = companies - sample_ids
        print(f"  companies with >=1 candidate : {frac(len(in_sample), n_sample)}")
        print(f"  companies with 'owns' verdict: {frac(len(owns_companies & sample_ids), n_sample)}")
        if outside:
            print(f"  WARNING: {len(outside)} audited compan{'y' if len(outside) == 1 else 'ies'} "
                  f"not in the sample file - excluded from coverage")
        print("  (which of the two numerators is 'coverage' is a judgement call, not this script's)")

    # --------------------------------------------------------------- precision
    print("\n=== precision (ownership, hand-audited) " + "=" * 25)
    n_aud = len(audited)
    if n_aud == 0:
        print("  no rows carry a verdict yet -> precision UNAVAILABLE.")
    else:
        k = len(owns)
        lo, hi = wilson(k, n_aud)
        print(f"  audited rows           : {n_aud} of {len(rows)} sheet rows")
        print(f"  verdict == 'owns'      : {frac(k, n_aud)}")
        print(f"  Wilson 95% CI          : [{lo:.3f}, {hi:.3f}]  (n={n_aud}, not the normal approximation)")
        print(f"  verdict == 'not_owns'  : {frac(len(not_owns), n_aud)}")
        print(f"  verdict == 'ambiguous' : {frac(len(ambiguous), n_aud)}")
        print(f"  verdict == 'not_registered': {frac(len(not_registered), n_aud)}")
        if ambiguous:
            print("  NOTE: ambiguous rows are counted in the denominator but not as successes.")
            print("        Excluding them instead would raise the fraction - say which you did.")
    if unknown:
        for v, rs in sorted(unknown.items()):
            print(f"  *** UNRECOGNISED verdict {v!r} on {len(rs)} row(s) - "
                  f"expected {' | '.join(VERDICTS)} ***")

    # ------------------------------------------------------- rejection funnel
    print("\n=== rejection breakdown: checksum -> EORI -> ownership " + "=" * 10)
    if candidates is None:
        print("  stage 1 (checksum): raw pre-checksum candidates not supplied (--candidates)")
        print("                      -> checksum pass/fail counts UNAVAILABLE")
    else:
        from checksum import is_valid

        raw = [v for v in load_column(candidates, "vat", "vat_number", "vrn") if v]
        passed = [v for v in raw if is_valid(v)]
        print(f"  stage 1 (checksum) : input {len(raw)} extracted candidate(s)")
        print(f"                       passed  {frac(len(passed), len(raw))}")
        print(f"                       dropped {frac(len(raw) - len(passed), len(raw))}")

    ev = {"true": 0, "false": 0, "": 0}
    for r in rows:
        ev[(r.get("eori_valid") or "").strip().lower() if
           (r.get("eori_valid") or "").strip().lower() in ("true", "false") else ""] += 1
    print(f"  stage 2 (EORI)     : input {len(rows)} sheet row(s)")
    print(f"                       eori_valid=true  {frac(ev['true'], len(rows))}")
    print(f"                       eori_valid=false {frac(ev['false'], len(rows))}")
    print(f"                       not checked      {frac(ev[''], len(rows))}")
    traders = sum(1 for r in rows if (r.get("eori_trader_name") or "").strip())
    print(f"                       of the valid ones, trader name shared: {frac(traders, ev['true'])}")
    print("                       (that share bounds how much ownership testing EORI alone can do)")

    print(f"  stage 3 (ownership): input {n_aud} audited row(s)")
    print(f"                       accepted 'owns'     {frac(len(owns), n_aud)}")
    print(f"                       rejected 'not_owns' {frac(len(not_owns), n_aud)}")
    print(f"                       'ambiguous'         {frac(len(ambiguous), n_aud)}")
    print(f"                       'not_registered'    {frac(len(not_registered), n_aud)}")
    unaudited = len(rows) - n_aud
    if unaudited:
        print(f"  NOT YET AUDITED    : {frac(unaudited, len(rows))} sheet rows carry no verdict")

    # ----------------------------------------------------------- ambiguous rows
    print("\n=== ambiguous rows, in full " + "=" * 37)
    if not ambiguous:
        print("  none")
    else:
        print(f"  {len(ambiguous)} row(s). These are the interesting ones - a disagreement the")
        print("  name/address test could not settle either way.\n")
        for i, r in enumerate(ambiguous, 1):
            print(f"  [{i}] company_number : {r.get('company_number','')}")
            print(f"      vat            : {r.get('vat','')}")
            print(f"      company_name   : {field(r, 'company_name')}")
            print(f"      hmrc_name      : {field(r, 'hmrc_name')}")
            print(f"      ch_postcode    : {field(r, 'ch_postcode') or '-'}   "
                  f"hmrc_postcode: {field(r, 'hmrc_postcode') or '-'}")
            print(f"      ch_address     : {field(r, 'ch_address') or '-'}")
            print(f"      hmrc_address   : {field(r, 'hmrc_address') or '-'}")
            print(f"      source         : {field(r, 'source') or '-'}")
            print(f"      eori_valid     : {r.get('eori_valid','')}   "
                  f"eori_trader_name: {r.get('eori_trader_name','')}")
            print(f"      checked_at     : {field(r, 'checked_at')}")
            print(f"      notes          : {r.get('notes','')}")
            print()
    return 0


# --------------------------------------------------------------------------


def _wilson_via_quadratic(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """
    The same interval by a different route, for the self-test to check against.

    Wilson's interval is by definition the set of p0 satisfying
        |p_hat - p0| / sqrt(p0(1-p0)/n) <= z
    which rearranges to the roots of
        (n + z²)·p0² - (2k + z²)·p0 + k²/n = 0

    That is independent algebra, not a restatement of `wilson()`, so agreement
    between the two is real evidence rather than a tautology. This is the check
    that matters: hardcoded constants only test whether someone typed a number
    from a table correctly, and the first version of this test failed precisely
    because the constants were wrong while the implementation was right.
    """
    a = n + z * z
    b = -(2.0 * k + z * z)
    c = k * k / n
    disc = b * b - 4 * a * c
    root = math.sqrt(max(0.0, disc))
    return ((-b - root) / (2 * a), (-b + root) / (2 * a))


def self_test() -> int:
    fails = []
    cases = [(47, 50), (50, 50), (0, 50), (470, 500), (1, 3), (25, 50), (1, 1), (3, 7)]

    for k, n in cases:
        got = wilson(k, n)
        want = _wilson_via_quadratic(k, n)
        if not all(abs(a - b) < 1e-9 for a, b in zip(got, want)):
            fails.append(f"wilson({k},{n}) = [{got[0]:.9f}, {got[1]:.9f}] but independent "
                         f"derivation gives [{want[0]:.9f}, {want[1]:.9f}]")
        if not (0.0 <= got[0] <= got[1] <= 1.0):
            fails.append(f"wilson({k},{n}) = {got} left [0,1] or is inverted - "
                         f"the failure mode the normal approximation has")

    # Two anchors, taken from the verified implementation rather than from memory.
    lo, hi = wilson(47, 50)
    if not (abs(lo - 0.837829083) < 1e-6 and abs(hi - 0.979385030) < 1e-6):
        fails.append(f"wilson(47,50) drifted: [{lo:.9f}, {hi:.9f}]")
    if abs(wilson(50, 50)[1] - 1.0) > 1e-12:
        fails.append("wilson(50,50) upper bound is not exactly 1.0")

    if wilson(0, 0) != (0.0, 1.0):
        fails.append(f"wilson(0,0) = {wilson(0, 0)}, expected the uninformative [0.0, 1.0]")

    w50, w500 = wilson(47, 50), wilson(470, 500)
    if not (w50[1] - w50[0]) > (w500[1] - w500[0]):
        fails.append("interval did not narrow as n grew at constant p")

    if frac(47, 50) != "47/50 = 0.940":
        fails.append(f"frac(47,50) = {frac(47,50)!r}, expected '47/50 = 0.940'")
    if "n/a" not in frac(0, 0):
        fails.append("frac with a zero denominator must not print a ratio")

    for f in fails:
        print(f"  FAIL  {f}")
    if not fails:
        print(f"  checked {len(cases)} cases against an independent derivation of the")
        print("  interval (quadratic roots), all agreeing to 1e-9:\n")
        for k, n in cases:
            lo, hi = wilson(k, n)
            print(f"    {k:>3}/{n:<3}  {frac(k, n):<18}  [{lo:.6f}, {hi:.6f}]")
        print("\n  boundaries hold: 50/50 -> upper exactly 1.0, 0/50 -> lower exactly 0.0")
        print("  all self-tests pass")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    ap.add_argument("--sample", type=Path, default=None,
                    help="frozen sample CSV, for the coverage denominator")
    ap.add_argument("--candidates", type=Path, default=None,
                    help="raw pre-checksum candidates, for the stage-1 funnel counts")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.sheet.exists():
        raise SystemExit(f"no audit sheet at {args.sheet}\n"
                         f"generate one with: python src/audit_worklist.py --candidates ...")
    sample = args.sample
    if sample is None and DEFAULT_SAMPLE.exists():
        sample = DEFAULT_SAMPLE
    if sample is not None and not sample.exists():
        raise SystemExit(f"no sample file at {sample}")
    if args.candidates is not None and not args.candidates.exists():
        raise SystemExit(f"no candidates file at {args.candidates}")
    return report(args.sheet, sample, args.candidates)


if __name__ == "__main__":
    sys.exit(main())
