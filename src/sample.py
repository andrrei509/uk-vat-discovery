"""
Draw and freeze the sample. Everything downstream joins to this file.

Freezing is the point
---------------------
A sample that changes when you rerun the script is not a sample, it is a
convenience draw, and every number computed against it becomes unfalsifiable.
So:

  - `data/sample/sample.csv` is refused if it already exists, unless --force.
  - The same seed must produce a **byte-identical** file. `--check-determinism`
    writes the sample twice to temp paths and diffs them.
  - The header block records the seed, the frame size, the snapshot SHA256 and
    every exclusion count, so the file explains its own provenance.

There is deliberately **no timestamp** in the header. A generated-at line would
make two runs of the same seed differ, which would destroy the one property most
worth having. The seed plus the snapshot hash identify the run.

The frame depends on the Companies House snapshot, so the snapshot's SHA256 is
part of the sample's identity: the same seed against a different month's
snapshot is a different sample, and the header makes that visible.

Usage
-----
    python src/sample.py                      # draws, refuses to overwrite
    python src/sample.py --force              # redraws over an existing file
    python src/sample.py --check-determinism  # two runs, byte-compared
    python src/sample.py --dry-run            # counts only, writes nothing
"""

from __future__ import annotations

import argparse
import csv
import filecmp
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "data" / "sample" / "sample.csv"
REPORT_PATH = REPO_ROOT / "notes" / "sample_design_output.txt"

SEED = 20260820
N = 500
N_HOLDOUT = 50

COLUMNS = [
    "company_number", "company_name", "reg_address_line1", "reg_post_town",
    "reg_postcode", "sic_code", "sic_text", "accounts_category",
    "incorporation_date", "sector_group", "size_band", "age_band", "holdout",
]

# SIC divisions (leading 2 digits of SICCode.SicText_1) per sector group.
SECTOR_GROUPS = {
    "manufacturing":       list(range(10, 34)),
    "construction_trades": list(range(41, 44)),
    "wholesale_retail":    list(range(45, 48)),
    "transport_logistics": list(range(49, 54)),
    "business_services":   [62] + list(range(69, 75)) + [78, 82],
}
SECTOR_ORDER = list(SECTOR_GROUPS)

SIZE_BANDS = {
    "micro":  ["MICRO ENTITY"],
    "small":  ["TOTAL EXEMPTION FULL", "TOTAL EXEMPTION SMALL",
               "UNAUDITED ABRIDGED", "AUDITED ABRIDGED", "SMALL"],
    "larger": ["FULL", "MEDIUM", "GROUP", "AUDIT EXEMPTION SUBSIDIARY",
               "FILING EXEMPTION SUBSIDIARY"],
    "unknown": ["NO ACCOUNTS FILED", "ACCOUNTS TYPE NOT AVAILABLE",
                "PARTIAL EXEMPTION"],
}
SIZE_ORDER = ["micro", "small", "larger", "unknown"]
AGE_ORDER = ["young", "mid", "old", "unknown"]

# SIC codes excluded outright. 74990 matters because division 74 otherwise falls
# inside business_services, so without this it would be swept in.
EXCLUDED_SIC = ["99999", "74990"]


def sql_quote_list(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def sector_case() -> str:
    whens = "\n".join(
        f"             WHEN sic_div IN ({', '.join(str(d) for d in divs)}) THEN '{name}'"
        for name, divs in SECTOR_GROUPS.items()
    )
    return f"CASE\n{whens}\n             ELSE NULL END"


def size_case() -> str:
    whens = "\n".join(
        f"             WHEN accounts_category IN ({sql_quote_list(cats)}) THEN '{band}'"
        for band, cats in SIZE_BANDS.items()
    )
    # Anything not listed lands in 'unknown' and is counted separately below.
    return f"CASE\n{whens}\n             ELSE 'unknown' END"


def build_views(con) -> None:
    """
    `base` = Active rows with SIC split out and bands derived.
    `frame`= base after every exclusion. The sample is drawn from `frame`.
    """
    con.execute(f"""
        CREATE VIEW base AS
        WITH raw AS (
            SELECT
                CompanyNumber                         AS company_number,
                CompanyName                           AS company_name,
                "RegAddress.AddressLine1"             AS reg_address_line1,
                "RegAddress.PostTown"                 AS reg_post_town,
                "RegAddress.PostCode"                 AS reg_postcode,
                "Accounts.AccountCategory"            AS accounts_category,
                IncorporationDate                     AS incorporation_date,
                trim(coalesce("SICCode.SicText_1", '')) AS sic_raw,
                CompanyStatus                         AS company_status
            FROM ch
        ), split AS (
            SELECT *,
                CASE WHEN position(' - ' IN sic_raw) > 0
                     THEN trim(substr(sic_raw, 1, position(' - ' IN sic_raw) - 1))
                     ELSE sic_raw END AS sic_code,
                CASE WHEN position(' - ' IN sic_raw) > 0
                     THEN trim(substr(sic_raw, position(' - ' IN sic_raw) + 3))
                     ELSE '' END      AS sic_text
            FROM raw
        )
        SELECT *,
            TRY_CAST(substr(sic_code, 1, 2) AS INTEGER) AS sic_div,
            TRY_CAST(substr(incorporation_date, 7, 4) AS INTEGER) AS incorp_year
        FROM split
    """)
    con.execute(f"""
        CREATE VIEW banded AS
        SELECT *,
            {sector_case()} AS sector_group,
            {size_case()}   AS size_band,
            CASE WHEN incorp_year IS NULL       THEN 'unknown'
                 WHEN incorp_year >= 2024       THEN 'young'
                 WHEN incorp_year >= 2016       THEN 'mid'
                 ELSE 'old' END AS age_band
        FROM base
    """)
    con.execute(f"""
        CREATE VIEW frame AS
        SELECT * FROM banded
        WHERE company_status = 'Active'
          AND sic_raw <> '' AND sic_raw <> 'None Supplied'
          AND sic_code NOT IN ({sql_quote_list(EXCLUDED_SIC)})
          AND accounts_category <> 'DORMANT'
          AND sector_group IS NOT NULL
    """)


def exclusion_waterfall(con) -> list[tuple[str, int, int]]:
    """
    (label, removed_here, remaining) applying exclusions in a fixed order.

    Reported as a waterfall rather than independently, because the exclusions
    overlap heavily — a dormant company with SIC 99999 is caught by two of them —
    and independent counts would sum to more than the rows actually dropped.
    Each `removed_here` is therefore "removed by this rule, of what survived the
    rules above it".
    """
    steps = [
        ("all rows in snapshot",        "TRUE"),
        ("CompanyStatus = 'Active'",    "company_status = 'Active'"),
        ("SIC present",                 "sic_raw <> '' AND sic_raw <> 'None Supplied'"),
        ("SIC not 99999 / 74990",       f"sic_code NOT IN ({sql_quote_list(EXCLUDED_SIC)})"),
        ("accounts not DORMANT",        "accounts_category <> 'DORMANT'"),
        ("SIC division is supplier-like", "sector_group IS NOT NULL"),
    ]
    out, where, prev = [], [], None
    for label, cond in steps:
        where.append(cond)
        n = con.execute(f"SELECT count(*) FROM banded WHERE {' AND '.join(where)}").fetchone()[0]
        out.append((label, 0 if prev is None else prev - n, n))
        prev = n
    return out


def allocate(counts: dict, n: int) -> dict:
    """
    Proportional allocation, then fix rounding drift.

    round(n * share) per cell will not sum to n. The drift is corrected on the
    largest cells first, which keeps the allocation closest to proportional and
    is deterministic given a deterministic cell order. Allocation is also capped
    at the cell's own size, since a cell cannot yield more rows than it has.
    """
    total = sum(counts.values())
    alloc = {k: min(counts[k], round(n * counts[k] / total)) for k in counts}
    # Largest cells first, ties broken by key so the order never depends on
    # dict insertion or parquet row order.
    order = sorted(counts, key=lambda k: (-counts[k], k))
    guard = 0
    while sum(alloc.values()) != n and guard < 10_000:
        guard += 1
        drift = n - sum(alloc.values())
        for k in order:
            if drift > 0 and alloc[k] < counts[k]:
                alloc[k] += 1
                break
            if drift < 0 and alloc[k] > 0:
                alloc[k] -= 1
                break
        else:
            break
    return alloc


def draw(con, seed: int, n: int, n_holdout: int):
    """Returns (rows, cell_counts, alloc, warnings)."""
    import numpy as np

    warnings = []

    # Checked against `frame`, not against all Active rows. DORMANT is absent
    # from every size band on purpose because the frame excludes it outright, so
    # scanning pre-exclusion rows would report it as an unlisted category and
    # bury a real warning under a false one.
    unlisted = con.execute(f"""
        SELECT accounts_category, count(*) AS n
        FROM frame
        WHERE accounts_category NOT IN ({sql_quote_list(
              [c for cats in SIZE_BANDS.values() for c in cats])})
        GROUP BY 1 ORDER BY n DESC
    """).fetchall()
    if unlisted:
        total = sum(r[1] for r in unlisted)
        warnings.append(
            f"WARNING: {total} active row(s) have an accounts category not in any "
            f"size band; they were placed in 'unknown': "
            + ", ".join(f"{c!r}={k}" for c, k in unlisted))

    unknown_age = con.execute(
        "SELECT count(*) FROM frame WHERE age_band = 'unknown'").fetchone()[0]
    if unknown_age:
        warnings.append(f"WARNING: {unknown_age} frame row(s) have an unparseable "
                        f"IncorporationDate and were placed in age_band 'unknown'")

    cell_counts = {}
    for sector in SECTOR_ORDER:
        for size in SIZE_ORDER:
            cell_counts[(sector, size)] = con.execute(
                "SELECT count(*) FROM frame WHERE sector_group = ? AND size_band = ?",
                [sector, size]).fetchone()[0]

    nonempty = {k: v for k, v in cell_counts.items() if v > 0}
    alloc_nonempty = allocate(nonempty, n)
    alloc = {k: alloc_nonempty.get(k, 0) for k in cell_counts}

    rng = np.random.default_rng(seed)
    picked: list[tuple] = []
    # Fixed cell order so the RNG is consumed identically every run.
    for key in sorted(alloc, key=lambda k: (SECTOR_ORDER.index(k[0]),
                                            SIZE_ORDER.index(k[1]))):
        k = alloc[key]
        if k == 0:
            continue
        sector, size = key
        # ORDER BY company_number: the parquet's own row order must not leak in.
        ids = [r[0] for r in con.execute(
            "SELECT company_number FROM frame WHERE sector_group = ? AND size_band = ? "
            "ORDER BY company_number", [sector, size]).fetchall()]
        idx = rng.choice(len(ids), size=k, replace=False)
        picked.extend((ids[i], sector, size) for i in sorted(int(i) for i in idx))

    # Holdout, spread across strata in proportion to what each cell contributed.
    per_cell: dict = {}
    for cid, sector, size in picked:
        per_cell.setdefault((sector, size), []).append(cid)
    hold_alloc = allocate({k: len(v) for k, v in per_cell.items()}, n_holdout)
    holdout: set[str] = set()
    for key in sorted(per_cell, key=lambda k: (SECTOR_ORDER.index(k[0]),
                                               SIZE_ORDER.index(k[1]))):
        k = hold_alloc.get(key, 0)
        if k <= 0:
            continue
        ids = per_cell[key]
        idx = rng.choice(len(ids), size=min(k, len(ids)), replace=False)
        holdout.update(ids[i] for i in (int(i) for i in idx))

    ids = [p[0] for p in picked]
    placeholders = ", ".join("?" for _ in ids)
    detail = {r[0]: r for r in con.execute(f"""
        SELECT company_number, company_name, reg_address_line1, reg_post_town,
               reg_postcode, sic_code, sic_text, accounts_category,
               incorporation_date, sector_group, size_band, age_band
        FROM frame WHERE company_number IN ({placeholders})
    """, ids).fetchall()}

    rows = []
    for cid, _, _ in picked:
        d = detail[cid]
        rows.append({
            "company_number": d[0], "company_name": d[1] or "",
            "reg_address_line1": d[2] or "", "reg_post_town": d[3] or "",
            "reg_postcode": d[4] or "", "sic_code": d[5] or "",
            "sic_text": d[6] or "", "accounts_category": d[7] or "",
            "incorporation_date": d[8] or "", "sector_group": d[9],
            "size_band": d[10], "age_band": d[11],
            "holdout": "True" if cid in holdout else "False",
        })
    # Deterministic file order, independent of draw order.
    rows.sort(key=lambda r: r["company_number"])
    return rows, cell_counts, alloc, warnings


def render_report(waterfall, cell_counts, alloc, rows, warnings, seed, n,
                  snapshot_sha, snapshot_name, numpy_version) -> str:
    buf = io.StringIO()
    w = buf.write
    frame_size = waterfall[-1][2]

    w("=== frame construction " + "=" * 43 + "\n")
    w(f"  snapshot: {snapshot_name}\n  sha256  : {snapshot_sha}\n\n")
    w(f"  {'step':<34} {'removed':>12} {'remaining':>12}\n")
    for i, (label, removed, remaining) in enumerate(waterfall):
        shown = "-" if i == 0 else f"{removed:,}"
        w(f"  {label:<34} {shown:>12} {remaining:>12,}\n")
    w(f"\n  FRAME SIZE: {frame_size:,}\n")
    w("  (waterfall: each 'removed' is of what survived the rules above it,\n"
      "   because the exclusions overlap and independent counts would double-count)\n")

    for wn in warnings:
        w(f"\n  {wn}\n")

    w("\n=== marginal: by sector " + "=" * 42 + "\n")
    for s in SECTOR_ORDER:
        fc = sum(v for k, v in cell_counts.items() if k[0] == s)
        dc = sum(1 for r in rows if r["sector_group"] == s)
        w(f"  {s:<22} frame {fc:>10,}  ({fc / frame_size:6.2%})   drawn {dc:>4}\n")

    w("\n=== marginal: by size band " + "=" * 39 + "\n")
    for s in SIZE_ORDER:
        fc = sum(v for k, v in cell_counts.items() if k[1] == s)
        dc = sum(1 for r in rows if r["size_band"] == s)
        w(f"  {s:<22} frame {fc:>10,}  ({fc / frame_size:6.2%})   drawn {dc:>4}\n")

    w("\n=== marginal: by age band (recorded, NOT stratified on) " + "=" * 10 + "\n")
    for a in AGE_ORDER:
        dc = sum(1 for r in rows if r["age_band"] == a)
        if dc or a != "unknown":
            w(f"  {a:<22} drawn {dc:>4}\n")

    w("\n=== 5 x 4 cells: frame count / allocated " + "=" * 25 + "\n")
    w(f"  {'sector':<22}" + "".join(f"{s:>18}" for s in SIZE_ORDER) + f"{'total':>10}\n")
    for sec in SECTOR_ORDER:
        line = f"  {sec:<22}"
        for size in SIZE_ORDER:
            line += f"{cell_counts[(sec, size)]:>13,}/{alloc[(sec, size)]:<4}"
        line += f"{sum(alloc[(sec, s)] for s in SIZE_ORDER):>10}"
        w(line + "\n")
    w(f"  {'total':<22}" + "".join(
        f"{sum(alloc[(sec, s)] for sec in SECTOR_ORDER):>18}" for s in SIZE_ORDER)
      + f"{sum(alloc.values()):>10}\n")

    zeros = [k for k, v in alloc.items() if v == 0]
    w("\n=== cells that got zero " + "=" * 42 + "\n")
    if not zeros:
        w("  none - all 20 cells received at least one row\n")
    else:
        for sec, size in sorted(zeros, key=lambda k: (SECTOR_ORDER.index(k[0]),
                                                      SIZE_ORDER.index(k[1]))):
            fc = cell_counts[(sec, size)]
            why = "cell is empty in the frame" if fc == 0 else \
                  f"cell has {fc:,} rows but proportional share rounded to 0"
            w(f"  {sec} x {size}: {why}\n")

    w("\n=== draw " + "=" * 56 + "\n")
    w(f"  seed          : {seed}\n  requested n   : {n}\n  rows drawn    : {len(rows)}\n")
    w(f"  holdout rows  : {sum(1 for r in rows if r['holdout'] == 'True')}\n")
    w(f"  distinct co no: {len({r['company_number'] for r in rows})}\n")
    w(f"  numpy         : {numpy_version}  (Generator/PCG64 stream)\n")
    return buf.getvalue()


def header_block(seed, n, frame_size, waterfall, snapshot_name, snapshot_sha,
                 numpy_version) -> list[str]:
    lines = [
        "# Frozen sample - do not edit by hand, do not re-draw without --force.",
        f"# seed={seed}  n={n}  frame_size={frame_size}",
        f"# snapshot={snapshot_name}",
        f"# snapshot_sha256={snapshot_sha}",
        f"# numpy={numpy_version} (default_rng/PCG64)",
        "# stratified on sector_group x size_band (20 cells), proportional allocation.",
        "# age_band is recorded but NOT stratified on.",
        "# exclusions applied in order (removed -> remaining):",
    ]
    for label, removed, remaining in waterfall:
        lines.append(f"#   {label}: removed {removed} -> remaining {remaining}")
    lines.append("# no generated-at timestamp on purpose: the same seed must give a"
                 " byte-identical file.")
    return lines


def write_sample(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        for line in header:
            fh.write(line + "\n")
        wr = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)


def run(seed: int, n: int, n_holdout: int):
    from companies_house import connect, find_download, find_source, sha256
    import numpy as np

    zip_path = find_download()
    snapshot_name = zip_path.name if zip_path else "(no zip present)"
    snapshot_sha = sha256(zip_path) if zip_path else "UNKNOWN"

    con = connect(find_source())
    build_views(con)
    waterfall = exclusion_waterfall(con)
    rows, cell_counts, alloc, warnings = draw(con, seed, n, n_holdout)
    report = render_report(waterfall, cell_counts, alloc, rows, warnings, seed, n,
                           snapshot_sha, snapshot_name, np.__version__)
    header = header_block(seed, n, waterfall[-1][2], waterfall, snapshot_name,
                          snapshot_sha, np.__version__)
    return rows, header, report


def main() -> int:
    ap = argparse.ArgumentParser(description="Draw and freeze the sample")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--n", type=int, default=N)
    ap.add_argument("--holdout", type=int, default=N_HOLDOUT)
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing frozen sample")
    ap.add_argument("--dry-run", action="store_true", help="counts only, write nothing")
    ap.add_argument("--check-determinism", action="store_true",
                    help="draw twice into temp files and byte-compare them")
    args = ap.parse_args()

    if args.check_determinism:
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td) / "a.csv", Path(td) / "b.csv"
            for p in (a, b):
                rows, header, _ = run(args.seed, args.n, args.holdout)
                write_sample(p, header, rows)
            same = filecmp.cmp(a, b, shallow=False)
            print(f"seed {args.seed}, two independent runs:")
            print(f"  {a.name}: {a.stat().st_size} bytes")
            print(f"  {b.name}: {b.stat().st_size} bytes")
            print(f"  byte-identical: {same}")
            return 0 if same else 1

    if args.out.exists() and not args.force and not args.dry_run:
        print("=" * 72, file=sys.stderr)
        print("REFUSING TO OVERWRITE THE FROZEN SAMPLE", file=sys.stderr)
        print("=" * 72, file=sys.stderr)
        print(f"{args.out} already exists.\n", file=sys.stderr)
        print("This file is frozen on purpose. Every downstream number - coverage,",
              file=sys.stderr)
        print("precision, the rejection breakdown - is computed against it. Redrawing",
              file=sys.stderr)
        print("it silently would invalidate those numbers while leaving them looking",
              file=sys.stderr)
        print("fine, and any company added after the fact is contamination the brief",
              file=sys.stderr)
        print("explicitly warns about.\n", file=sys.stderr)
        print("If you genuinely mean to redraw, pass --force and record why in",
              file=sys.stderr)
        print("DECISIONS.md.", file=sys.stderr)
        return 1

    rows, header, report = run(args.seed, args.n, args.holdout)
    print(report)

    if args.dry_run:
        print("--dry-run: nothing written")
        return 0

    write_sample(args.out, header, rows)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"wrote {args.out} ({len(rows)} rows)")
    print(f"wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
