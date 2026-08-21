"""
How high could this pipeline go, as opposed to how high it got?

The achieved figure is 6 of 500. That number is a property of one run with one
set of bugs and one path list. The ceiling is a property of the *method*: if
every company that has a website were crawled successfully, how many VAT
numbers would exist to be found?

    ceiling = (share of companies with a findable website)
              x (share of crawled sites that publish a VAT number)

Both factors come from small hand-audited samples, so both carry wide Wilson
intervals and the answer is a range, not a point.

The weighting matters more than the arithmetic
----------------------------------------------
`audit/domain_audit.csv` audited 5 rows from each of the four outcome groups.
Those groups are 39, 125, 41 and 295 companies. Counting the 20 audited rows
directly would let the 39-company group carry the same weight as the
295-company one, so each group's rate is weighted by its real size instead.

Why the verdict column is not used directly
-------------------------------------------
`verdict` answers "was the pipeline right", which is a different question from
"does this company have a website". A `no_domain` row marked `wrong` means the
company does have a site that the pipeline missed. A `strong` row marked
`wrong` means the pipeline found a site belonging to somebody else. Adding
those together would be meaningless, so the classification below is read from
the auditor's free-text note and recorded explicitly, one company at a time.

    python src/method_ceiling.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from metrics import wilson  # noqa: E402

AUDIT = REPO_ROOT / "audit" / "domain_audit.csv"

# Real size of each outcome group, from notes/domain_discovery_output.txt.
GROUP_SIZES = {"strong": 39, "weak": 125, "none": 41, "no_domain": 295}
SAMPLE_N = 500

# Publication rate: data/results/candidates.csv over the strong-match rows.
PUBLISHED, CRAWLED = 6, 39

# Does a website belonging to THIS company exist and can a person find it?
# Read from the auditor's note in audit/domain_audit.csv, company by company,
# so the judgement is inspectable rather than buried in a rule.
HAS_SITE = {
    "PHSC PLC": False,                              # different legal entity
    "BSN (LONDON) LIMITED": True,
    "ETI LOGISTICS SUPPORT LTD": True,
    "SOUTH YORKSHIRE BUILDERS LTD": True,
    "TIMGLOBAL LTD": True,
    "SURIYA JEWELS LIMITED": False,                 # placeholder, owner unknown
    "FISH! RESTAURANTS LTD": True,                  # fishboroughmarket.com
    "MAPACH LIMITED": False,                        # domain for sale
    "ADVANCED ROOF TESTING LTD": True,
    "UNINHIBITED DEVELOPMENT SOLUTIONS LTD": True,  # uni-concept.netlify.app
    "PTERO-BYTE LIMITED": True,                     # ptero-byte.com
    "OUR WORLD OUR SAY": False,                     # former domain offline
    "1COUNSEL LIMITED": False,
    "JBB CONSULTANTS LIMITED": False,
    "DID TEACH LIMITED": True,                      # didteach.com is theirs
    "ODYSSEY ELECTRICAL SERVICES LTD": True,        # exists, unresponsive
    "ACE ELECTRICIANS LIMITED": False,
    "MPL GROUNDWORK & BUILDING LIMITED": True,      # exists, suspended
    "SBA-SPI LIMITED": False,
    "AJE TECH LTD": True,                           # exists, crawler got 403
}

# Sites that exist but served nothing on the day. Counted as having a site,
# because the question is what the method could reach, not what it reached.
UNREACHABLE_BUT_REAL = ["ODYSSEY ELECTRICAL SERVICES LTD",
                        "MPL GROUNDWORK & BUILDING LIMITED",
                        "AJE TECH LTD"]


def load_audit() -> list[dict]:
    lines = [ln for ln in AUDIT.read_text(encoding="utf-8-sig").splitlines()
             if not ln.lstrip().startswith("#")]
    return [dict(r) for r in csv.DictReader(lines)]


def main() -> int:
    rows = load_audit()
    unknown = [r["company_name"] for r in rows if r["company_name"] not in HAS_SITE]
    if unknown:
        raise SystemExit("audit sheet has rows this script has no judgement for: "
                         + ", ".join(unknown))

    print("=== 1. publication rate " + "=" * 43)
    plo, phi = wilson(PUBLISHED, CRAWLED)
    print(f"  {PUBLISHED}/{CRAWLED} = {PUBLISHED / CRAWLED:.3f}"
          f"    Wilson 95% [{plo:.3f}, {phi:.3f}]")

    print("\n=== 2. share of the 500 with a findable website " + "=" * 19)
    print(f"  {'group':<11}{'audited':>8}{'has site':>10}{'rate':>8}"
          f"{'Wilson 95%':>20}{'size':>7}")
    point = lo_sum = hi_sum = 0.0
    for g, size in GROUP_SIZES.items():
        grp = [r for r in rows if r["match_strength"] == g]
        k, n = sum(1 for r in grp if HAS_SITE[r["company_name"]]), len(grp)
        lo, hi = wilson(k, n)
        point += size * (k / n); lo_sum += size * lo; hi_sum += size * hi
        print(f"  {g:<11}{n:>8}{k:>10}{k / n:>8.3f}"
              f"{f'[{lo:.3f}, {hi:.3f}]':>20}{size:>7}")
    wp, wl, wh = point / SAMPLE_N, lo_sum / SAMPLE_N, hi_sum / SAMPLE_N
    print(f"\n  weighted by real group size: {wp:.3f}"
          f"  (about {point:.0f} of {SAMPLE_N})")
    print(f"  range                      : [{wl:.3f}, {wh:.3f}]")
    print(f"  unweighted, for comparison : "
          f"{sum(1 for r in rows if HAS_SITE[r['company_name']])}/{len(rows)}")

    print("\n=== 3. ceiling " + "=" * 52)
    print(f"  point : {wp:.3f} x {PUBLISHED / CRAWLED:.3f} = "
          f"{wp * PUBLISHED / CRAWLED:.4f}"
          f"  ({wp * PUBLISHED / CRAWLED * 100:.1f}%, about "
          f"{wp * PUBLISHED / CRAWLED * SAMPLE_N:.0f} of {SAMPLE_N})")
    print(f"  range : [{wl * plo:.4f}, {wh * phi:.4f}]"
          f"  ({wl * plo * 100:.1f}% to {wh * phi * 100:.1f}%, "
          f"{wl * plo * SAMPLE_N:.0f} to {wh * phi * SAMPLE_N:.0f} of {SAMPLE_N})")
    print(f"  achieved: {PUBLISHED}/{SAMPLE_N} = {PUBLISHED / SAMPLE_N:.4f}"
          f"  ({PUBLISHED / SAMPLE_N * 100:.1f}%)")

    print("\n=== sensitivity " + "=" * 51)
    alt = point - sum(GROUP_SIZES[r["match_strength"]] / 5
                      for r in rows if r["company_name"] in UNREACHABLE_BUT_REAL)
    print(f"  counting the 3 exists-but-served-nothing sites as NO site:")
    print(f"    website share {alt / SAMPLE_N:.3f} instead of {wp:.3f}, "
          f"ceiling {alt / SAMPLE_N * PUBLISHED / CRAWLED * 100:.1f}% "
          f"instead of {wp * PUBLISHED / CRAWLED * 100:.1f}%")

    print("\n  Caveats, both unquantified, pulling in opposite directions:")
    print("   a) 6/39 is a floor: 15 paths per site, no PDFs opened.")
    print("   b) the 39 strong-match companies are not a random draw, and firms")
    print("      whose name maps cleanly to their domain may publish more often")
    print("      than the other 461.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
