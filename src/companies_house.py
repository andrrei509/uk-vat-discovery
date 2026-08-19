"""
Companies House bulk snapshot: load, profile, and build the frame the sample
will be drawn from.

Get the file
------------
    https://download.companieshouse.gov.uk/en_output.html
    -> "BasicCompanyDataAsOneFile-YYYY-MM-01.zip"

Measured for the 2026-08-01 snapshot this project used: 493,049,031 bytes
zipped (0.49 GB / 0.46 GiB), 2,803,365,091 bytes extracted (2.80 GB / 2.61 GiB).
Reproduce with `--hash`.

Put it in `data/companies_house/`. Do NOT commit it (see .gitignore); record
its filename, SHA256 and row count in DECISIONS.md instead so the run is
reproducible without the bulk.

Why DuckDB
----------
The CSV is 5,695,465 rows and 55 columns (measured on the 2026-08-01 snapshot
via `--hash`; earlier notes in this repo said ~5.4M, which the snapshot does not
support). pandas will read it, but it will eat several GB of RAM and every
exploratory question costs a full re-scan. DuckDB
queries the CSV (or a Parquet copy of it) directly, out of core, in SQL. That
is a tool choice you should be able to justify — the assignment explicitly
grades "are tools used to support the solution, or is the solution shaped
around the tool".

    python src/companies_house.py --profile
    python src/companies_house.py --to-parquet      # do this once

Measured on the 2026-08-01 snapshot: `--profile` takes **2.42 s** against the
parquet and **23.31 s** against the CSV, so the parquet is **9.6x faster** (best
of two runs each). Reproduce the comparison with `--no-parquet`, which forces the
CSV path even when a parquet exists:

    python src/companies_house.py --profile                # parquet
    python src/companies_house.py --profile --no-parquet    # CSV

Columns that matter for this project (of the 55; most are filing dates):
    CompanyName                     the string the customer is matching on
    CompanyNumber                   the join key
    CompanyStatus                   Active / Dissolved / Liquidation / ...
    CompanyCategory                 Private Limited, PLC, LLP, ...
    RegAddress.PostCode             address agreement test vs HMRC
    RegAddress.AddressLine1/2, PostTown, County
    SICCode.SicText_1..4            sector — drives VAT-registration probability
    IncorporationDate               age
    Accounts.AccountCategory        MICRO ENTITY / SMALL / FULL / DORMANT / ...
    Accounts.NextDueDate            liveness signal
    URI                             Companies House URL (NOT a company website)

Note what is NOT here: no website, no turnover, no VAT number, no employee
count. The absence of a website field is the hidden cost of any
crawl-the-company's-site strategy, and it deserves a paragraph of its own.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CH_DIR = REPO_ROOT / "data" / "companies_house"
PARQUET = CH_DIR / "companies.parquet"


def find_source(prefer_parquet: bool = True) -> Path:
    """
    Locate the snapshot: prefer parquet, then csv, then zip.

    `prefer_parquet=False` forces the CSV path, which exists so the "parquet is
    faster" claim can be measured rather than asserted (`--no-parquet`).
    """
    if prefer_parquet and PARQUET.exists():
        return PARQUET
    for pattern in ("*.csv", "*.zip"):
        hits = sorted(CH_DIR.glob(pattern))
        if hits:
            return hits[-1]
    raise SystemExit(
        f"No snapshot found in {CH_DIR}.\n"
        "Download BasicCompanyDataAsOneFile-*.zip from\n"
        "  https://download.companieshouse.gov.uk/en_output.html\n"
        f"and put it in {CH_DIR}"
    )


def find_download() -> Path | None:
    """
    The *downloaded* artefact, as opposed to anything we derived from it.

    `--hash` must report this one. The parquet is not byte-reproducible (it
    depends on the duckdb version and its compression settings), and the
    extracted CSV is just the zip's payload. The zip is what a third party
    would fetch from Companies House, so the zip is the reproducibility anchor.
    """
    hits = sorted(CH_DIR.glob("*.zip"))
    return hits[-1] if hits else None


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def unzip(zip_path: Path) -> Path:
    with zipfile.ZipFile(zip_path) as zf:
        name = zf.namelist()[0]
        out = CH_DIR / name
        if not out.exists():
            print(f"extracting {name} ...")
            zf.extract(name, CH_DIR)
        return out


def csv_header(path: Path) -> list[str]:
    """
    The header line, exactly as Companies House wrote it — raw bytes, not
    duckdb's cleaned-up version of it. `--header` prints this so the claim
    "CH ships 14 headers with a leading space" is checkable rather than folklore.
    """
    import csv

    with path.open(encoding="utf-8-sig", newline="") as fh:
        return next(csv.reader(fh))


def connect(source: Path):
    """
    Expose the snapshot as a view called `ch`.

    Two deliberate departures from `read_csv_auto`'s defaults, both of which
    are correctness fixes rather than preferences:

    1. **Column names are stripped of whitespace.** Companies House ships 14 of
       its 55 headers with a leading space (`--header` lists them), and one is
       ` CompanyNumber`
       — the join key for everything in this project. Left as-is,
       `WHERE CompanyNumber = ...` is a "column not found" error, and the
       version that works (`" CompanyNumber"`) is a trap nobody spots in
       review.

       As it happens duckdb 1.5.5 already trims header whitespace for us, so on
       this machine the projection below is a passthrough. It is still built
       explicitly, from duckdb's *own* reported column names rather than from
       the bytes in the file, because "does the CSV reader trim headers?" is an
       undocumented detail that has changed before and would fail silently at
       the join, not at the read. Names are otherwise untouched:
       `normalize_names=true` would lowercase them and rewrite
       `SICCode.SicText_1` to `siccode_sictext_1`, a bigger change than the
       problem warrants.

    2. **Every column is read as VARCHAR.** Type inference on a 2.8 GB file
       samples the head, so the inferred type depends on how much of the file
       it looked at — which makes the load non-reproducible in principle. It
       is also actively wrong here: dates arrive as `DD/MM/YYYY`, and if duckdb
       decides that is a DATE, then `substr(IncorporationDate, 7, 4)` — how
       `profile()` reads the year — silently returns fragments of an ISO string
       instead of the year. Strings in, explicit parsing where we need it, no
       silent reinterpretation of a field we are about to report a number from.
    """
    import duckdb

    con = duckdb.connect()
    if source.suffix == ".parquet":
        # The parquet was written from this same cleaned view, so its names are
        # already stripped and its columns are already VARCHAR.
        con.execute(f"CREATE VIEW ch AS SELECT * FROM read_parquet('{source}')")
        return con

    if source.suffix == ".zip":
        source = unzip(source)

    con.execute(
        f"""
        CREATE VIEW ch_raw AS
            SELECT * FROM read_csv('{source}', header=true, all_varchar=true,
                                   normalize_names=false)
        """
    )
    raw = [r[1] for r in con.execute("PRAGMA table_info('ch_raw')").fetchall()]
    clean = [c.strip() for c in raw]
    dupes = {c for c in clean if clean.count(c) > 1}
    if dupes:
        raise SystemExit(f"stripping header names collides on: {sorted(dupes)}")

    projection = ",\n                   ".join(
        f'"{r}" AS "{c}"' if r != c else f'"{c}"' for r, c in zip(raw, clean)
    )
    con.execute(f"CREATE VIEW ch AS SELECT {projection} FROM ch_raw")
    return con


def to_parquet(source: Path) -> None:
    con = connect(source)
    print(f"writing {PARQUET} ...")
    con.execute(f"COPY (SELECT * FROM ch) TO '{PARQUET}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    print("done. Subsequent runs will use the parquet automatically.")


def q(con, sql: str, title: str, limit: int = 25) -> None:
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))
    rows = con.execute(sql).fetchall()
    cols = [d[0] for d in con.description]
    widths = [max(len(str(c)), *(len(str(r[i])) for r in rows[:limit])) if rows else len(str(c))
              for i, c in enumerate(cols)]
    print("  " + "  ".join(str(c).ljust(w) for c, w in zip(cols, widths)))
    for r in rows[:limit]:
        print("  " + "  ".join(str(v).ljust(w) for v, w in zip(r, widths)))
    if len(rows) > limit:
        print(f"  ... {len(rows) - limit} more rows")


def profile(con) -> None:
    """
    First look at the data. Read every one of these numbers and write down what
    surprised you — Part 1 asks "what did you learn that wasn't obvious at the
    start", and this is the cheapest place to learn something.
    """
    q(con, "SELECT count(*) AS total_rows FROM ch", "size")

    q(con, "PRAGMA table_info('ch')", "columns", limit=100)

    q(con, """
        SELECT CompanyStatus, count(*) AS n,
               round(100.0*count(*)/sum(count(*)) OVER (), 2) AS pct
        FROM ch GROUP BY 1 ORDER BY n DESC
    """, "company status")

    q(con, """
        SELECT "Accounts.AccountCategory" AS accounts_category, count(*) AS n,
               round(100.0*count(*)/sum(count(*)) OVER (), 2) AS pct
        FROM ch WHERE CompanyStatus = 'Active'
        GROUP BY 1 ORDER BY n DESC
    """, "accounts category (ACTIVE only) - your main size proxy")

    q(con, """
        SELECT CompanyCategory, count(*) AS n
        FROM ch WHERE CompanyStatus = 'Active'
        GROUP BY 1 ORDER BY n DESC
    """, "legal form (ACTIVE only)")

    # 'None Supplied' would otherwise show up as a division called 'No'.
    q(con, """
        SELECT CASE WHEN "SICCode.SicText_1" = 'None Supplied' THEN '(none supplied)'
                    ELSE substr(trim("SICCode.SicText_1"), 1, 2) END AS sic_division,
               any_value("SICCode.SicText_1") AS example, count(*) AS n
        FROM ch
        WHERE CompanyStatus = 'Active' AND "SICCode.SicText_1" IS NOT NULL
        GROUP BY 1 ORDER BY n DESC
    """, "top SIC divisions (ACTIVE only)", limit=30)

    q(con, """
        SELECT CAST(substr(IncorporationDate, 7, 4) AS INT) AS year, count(*) AS n
        FROM ch WHERE CompanyStatus = 'Active'
        GROUP BY 1 HAVING year IS NOT NULL ORDER BY year DESC
    """, "incorporations by year (ACTIVE only)", limit=20)

    # Careful with "missing" here. Companies House does not leave SIC blank when
    # it doesn't have one — it writes the literal string 'None Supplied'. So the
    # obvious `IS NULL OR = ''` test returns zero and tells you SIC coverage is
    # perfect, which is wrong by 216k companies. Blank *addresses* really are
    # absent (they arrive as NULL, not ''), so the two fields need different
    # tests. Both matter: no SIC means no sector to reason about, and no
    # postcode means the HMRC address-agreement check cannot run at all.
    q(con, """
        SELECT
          count(*) AS active_total,
          count(*) FILTER (WHERE "RegAddress.PostCode" IS NULL) AS no_postcode,
          count(*) FILTER (WHERE "SICCode.SicText_1" IS NULL
                              OR "SICCode.SicText_1" = ''
                              OR "SICCode.SicText_1" = 'None Supplied') AS no_usable_sic,
          count(*) FILTER (WHERE "SICCode.SicText_1" LIKE '99999%') AS sic_dormant_code,
          count(*) FILTER (WHERE "SICCode.SicText_1" LIKE '74990%') AS sic_nontrading_code
        FROM ch WHERE CompanyStatus = 'Active'
    """, "data quality gaps (ACTIVE only)")

    # Registered-address collisions: the reason "HMRC address matches" is a
    # weaker signal than it looks. Accountants and formation agents register
    # thousands of companies at one address.
    q(con, """
        SELECT "RegAddress.AddressLine1" AS line1, "RegAddress.PostCode" AS postcode,
               count(*) AS companies_at_this_address
        FROM ch WHERE CompanyStatus = 'Active' AND "RegAddress.PostCode" <> ''
        GROUP BY 1,2 ORDER BY companies_at_this_address DESC
    """, "most-shared registered addresses - why address agreement is weak", limit=15)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--to-parquet", action="store_true")
    ap.add_argument("--hash", action="store_true",
                    help="print SHA256 of the downloaded zip + the row count")
    ap.add_argument("--sql", help="run an ad-hoc query against view `ch`")
    ap.add_argument("--header", action="store_true",
                    help="print the CSV header as Companies House wrote it")
    ap.add_argument("--no-parquet", action="store_true",
                    help="read the CSV even when a parquet exists; for timing comparisons")
    args = ap.parse_args()

    CH_DIR.mkdir(parents=True, exist_ok=True)

    if args.header:
        csvs = sorted(CH_DIR.glob("*.csv"))
        if not csvs:
            raise SystemExit(f"no extracted *.csv in {CH_DIR}; run --to-parquet first")
        raw = csv_header(csvs[-1])
        padded = [c for c in raw if c != c.strip()]
        for i, c in enumerate(raw):
            mark = "  <-- leading/trailing space" if c != c.strip() else ""
            print(f"{i:3d} {c!r}{mark}")
        print(f"\n{len(padded)} of {len(raw)} headers carry whitespace: "
              f"{[c for c in padded]}")
        return 0

    if args.hash:
        # Hash the download, not a derivative — see find_download().
        zip_path = find_download()
        if zip_path is None:
            raise SystemExit(f"no *.zip in {CH_DIR}; nothing to hash")
        print(f"file  : {zip_path.name}")
        print(f"bytes : {zip_path.stat().st_size}")
        print(f"sha256: {sha256(zip_path)}")
        con = connect(find_source())
        print(f"rows  : {con.execute('SELECT count(*) FROM ch').fetchone()[0]}")
        return 0

    source = find_source(prefer_parquet=not args.no_parquet)
    print(f"source: {source.name} ({source.stat().st_size / 1e9:.2f} GB)")

    if args.to_parquet:
        to_parquet(source)
        return 0

    con = connect(source)
    if args.sql:
        q(con, args.sql, "query", limit=50)
    else:
        profile(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())
