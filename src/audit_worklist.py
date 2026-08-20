"""
Build the manual-audit sheet — the Tier 3 ownership test, done by hand.

Why this exists
---------------
Tiers 1 and 2 are automated: the checksum says a number is *plausible*, the EORI
checker says it *exists*. Neither says the number belongs to the company we
attached it to. The only thing that does is HMRC's checker returning a
registered name and address that we compare against Companies House — and we
decided not to automate the public web form, so that comparison is typed in by
hand, one row at a time.

So this script's whole job is to make an hour of manual work as cheap and as
honest as possible: pre-fill everything a machine can know, leave exactly the
columns a human must fill, and never, ever lose a row someone already filled in.

The one rule that shapes the code
---------------------------------
**Hand-entered data is the most expensive data in the repo.** A regenerate that
silently reorders rows or blanks a verdict costs an hour that cannot be
recovered, and worse, it would do it invisibly. So:

  - existing rows are copied through byte-for-byte, in their existing order
  - new candidates are appended, never interleaved
  - a sheet with any filled row is backed up before it is rewritten
  - the write is atomic (temp file + replace), so an interrupted run cannot
    leave a half-written sheet

Usage
-----
    python src/audit_worklist.py --candidates data/results/candidates.csv
    python src/audit_worklist.py --candidates ... --max 100 --seed 20260819
    python src/audit_worklist.py --candidates ... --dry-run

Candidates CSV: needs `company_number` and `vat`. Optional and passed through if
present: `source_url`, `eori_valid`, `eori_trader_name`. Anything else is
ignored.

The sheet carries `#` comment lines above the header recording how it was built
(seed included, so a subsample is reproducible). `src/metrics.py` skips them.
"""

from __future__ import annotations

import argparse
import csv
import random
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "audit" / "manual_audit.csv"
DEFAULT_CANDIDATES = REPO_ROOT / "data" / "results" / "candidates.csv"

# Filled in by this script.
PREFILLED = [
    "company_number",
    "ch_name",
    "ch_address_line1",
    "ch_postcode",
    "vat",
    "source_url",
    "eori_valid",
    "eori_trader_name",
]

# Left blank, for a human with HMRC's checker open in a browser.
MANUAL = [
    "hmrc_checked_at",
    "hmrc_name",
    "hmrc_address",
    "hmrc_postcode",
    "verdict",
    "notes",
]

COLUMNS = PREFILLED + MANUAL
VERDICTS = ("owns", "not_owns", "ambiguous")

# The join key for a candidate. A company can legitimately appear twice with two
# different candidate numbers — that is a disagreement worth auditing, not a
# duplicate to collapse — so the key is the pair, not the company.
KEY = ("company_number", "vat")


# --------------------------------------------------------------------------
# CSV with comment lines


def read_sheet(path: Path) -> tuple[list[str], list[dict]]:
    """
    Return (preamble_lines, rows).

    The header row is located by looking for it, not by counting `#` lines. A
    sheet that has been round-tripped through Excel does not come back the way
    it was written: Excel pads a comment line out to the full column count
    (`# note,,,,,,,,,,,`) and can leave a separator row of bare commas above the
    header. Stopping at the first non-`#` line would make that separator the
    header, giving twelve empty column names, after which every lookup fails on
    a file that plainly contains the columns.
    """
    with path.open(encoding="utf-8-sig", newline="") as fh:
        lines = fh.read().splitlines(keepends=True)

    header_at = 0
    for i, line in enumerate(lines):
        cells = next(csv.reader([line.rstrip("\r\n")]), [])
        first = cells[0].strip() if cells else ""
        if first and not first.startswith("#"):
            header_at = i
            break
    comments = [l.rstrip("\r\n") for l in lines[:header_at]]
    rows = [dict(r) for r in csv.DictReader(lines[header_at:])]
    return comments, rows


def write_sheet(path: Path, comments: Iterable[str], rows: list[dict]) -> None:
    """
    Atomic write: build a temp file next to the target, then replace. An
    interrupted run leaves the previous sheet intact rather than a truncated one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # utf-8-sig, not utf-8: this sheet is opened in Excel and typed into by hand,
    # and Excel reads a BOM-less UTF-8 CSV as the system codepage — which turns
    # any non-ASCII company name into mojibake the moment it is saved back.
    # read_sheet() reads utf-8-sig, so the round trip is clean.
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        for c in comments:
            fh.write(c if c.startswith("#") else f"# {c}")
            fh.write("\n")
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r.get(c, "") or "" for c in COLUMNS})
    tmp.replace(path)


def is_filled(row: dict) -> bool:
    """True if a human has put anything in any manual column."""
    return any((row.get(c) or "").strip() for c in MANUAL)


# --------------------------------------------------------------------------
# Normalisation


def norm_company_number(raw: str) -> str:
    """
    Companies House numbers are 8 characters, zero-padded, and some carry an
    alpha prefix (SC, NI, RC, OC...). A candidates file that lost the padding
    would silently fail to join, so pad the all-digit ones back to 8.
    """
    v = (raw or "").strip().upper()
    return v.zfill(8) if v.isdigit() else v


def norm_vat(raw: str) -> str:
    """
    Canonical 9-digit VRN where possible — that is the form typed into HMRC's
    form. Falls back to the stripped input if it does not validate, so a
    candidate is never silently dropped here; filtering is Tier 1's job, not
    this script's.
    """
    v = (raw or "").strip()
    try:
        from checksum import normalise, validate

        parsed = validate(v)
        if parsed is not None:
            return parsed.normalised
        return normalise(v) or v
    except Exception:
        return v


def norm_bool(raw: str) -> str:
    v = (raw or "").strip().lower()
    if v in ("true", "1", "yes", "y", "t"):
        return "true"
    if v in ("false", "0", "no", "n", "f"):
        return "false"
    return ""


# --------------------------------------------------------------------------
# Companies House lookup


def ch_lookup(company_numbers: list[str]) -> dict[str, dict]:
    """
    name / address line 1 / postcode for each company number, from the snapshot.

    Queried in one pass with an explicit placeholder list rather than by
    string-interpolating company numbers into SQL.
    """
    if not company_numbers:
        return {}
    from companies_house import connect, find_source

    con = connect(find_source())
    placeholders = ", ".join("?" for _ in company_numbers)
    sql = f"""
        SELECT CompanyNumber,
               CompanyName,
               "RegAddress.AddressLine1" AS line1,
               "RegAddress.PostCode"    AS postcode
        FROM ch
        WHERE CompanyNumber IN ({placeholders})
    """
    out: dict[str, dict] = {}
    for num, name, line1, postcode in con.execute(sql, company_numbers).fetchall():
        out[num] = {
            "ch_name": name or "",
            "ch_address_line1": line1 or "",
            "ch_postcode": postcode or "",
        }
    return out


# --------------------------------------------------------------------------
# Build


def load_candidates(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = [dict(r) for r in csv.DictReader(fh)]
    if not rows:
        return []
    missing = [c for c in ("company_number", "vat") if c not in rows[0]]
    if missing:
        raise SystemExit(
            f"{path.name} is missing required column(s): {', '.join(missing)}\n"
            f"found: {', '.join(rows[0].keys())}"
        )
    out = []
    for r in rows:
        cn, vat = norm_company_number(r.get("company_number", "")), norm_vat(r.get("vat", ""))
        if not cn or not vat:
            continue
        out.append({
            "company_number": cn,
            "vat": vat,
            "source_url": (r.get("source_url") or "").strip(),
            "eori_valid": norm_bool(r.get("eori_valid", "")),
            "eori_trader_name": (r.get("eori_trader_name") or "").strip(),
        })
    return out


def dedupe(rows: list[dict]) -> tuple[list[dict], int]:
    """Collapse exact duplicate (company_number, vat) pairs, keeping the first."""
    seen: set[tuple] = set()
    out = []
    for r in rows:
        k = tuple(r[c] for c in KEY)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out, len(rows) - len(out)


def build(
    candidates_path: Path,
    out_path: Path,
    max_rows: int = 100,
    seed: int = 20260819,
    dry_run: bool = False,
) -> int:
    cands = load_candidates(candidates_path)
    cands, dupes = dedupe(cands)
    # Deterministic order, always — a subsample drawn from an unstable order is
    # not reproducible even with the seed recorded.
    cands.sort(key=lambda r: (r["company_number"], r["vat"]))

    existing_comments: list[str] = []
    existing: list[dict] = []
    if out_path.exists():
        existing_comments, existing = read_sheet(out_path)

    existing_keys = {tuple(r.get(c, "") for c in KEY) for r in existing}
    filled_count = sum(1 for r in existing if is_filled(r))

    fresh = [r for r in cands if tuple(r[c] for c in KEY) not in existing_keys]

    # The cap is on the whole sheet, not on each run — otherwise repeated runs
    # would grow it past the number anyone agreed to audit by hand.
    room = max(0, max_rows - len(existing))
    subsampled = False
    if len(fresh) > room:
        fresh = sorted(
            random.Random(seed).sample(fresh, room),
            key=lambda r: (r["company_number"], r["vat"]),
        )
        subsampled = True

    enriched = ch_lookup(sorted({r["company_number"] for r in fresh}))
    no_ch = 0
    for r in fresh:
        ch = enriched.get(r["company_number"])
        if ch is None:
            no_ch += 1
            ch = {"ch_name": "", "ch_address_line1": "", "ch_postcode": ""}
        r.update(ch)
        for c in MANUAL:
            r[c] = ""

    rows = existing + fresh

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    comments = existing_comments or [
        "# manual_audit sheet — Tier 3 ownership test, filled in by hand.",
        f"# verdict must be one of: {' | '.join(VERDICTS)}",
        "# generated by: python src/audit_worklist.py",
    ]
    comments = list(comments) + [
        f"# {'merged' if existing else 'created'} {now}: "
        f"+{len(fresh)} new row(s) from {candidates_path.name} "
        f"({len(cands)} candidate(s) after dedupe)"
        + (f"; subsample seed={seed}, cap={max_rows}" if subsampled else "")
    ]

    print(f"candidates file : {candidates_path}")
    print(f"  candidates    : {len(cands)}" + (f" (dropped {dupes} duplicate pair(s))" if dupes else ""))
    print(f"existing sheet  : {out_path if out_path.exists() else '(none)'}")
    print(f"  existing rows : {len(existing)}  of which filled: {filled_count}")
    print(f"  new rows added: {len(fresh)}")
    if subsampled:
        print(f"  SUBSAMPLED    : {len(fresh)} of the available new rows, seed={seed}, cap={max_rows}")
    if no_ch:
        print(f"  WARNING       : {no_ch} row(s) had no Companies House match; CH columns left blank")
    print(f"  sheet total   : {len(rows)}")

    if dry_run:
        print("\n--dry-run: nothing written")
        return 0

    if not fresh and existing:
        print("\nnothing new to add; sheet left untouched")
        return 0

    # Back up before touching a sheet that contains human work.
    if filled_count:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = out_path.with_name(f"{out_path.stem}.backup-{stamp}{out_path.suffix}")
        shutil.copy2(out_path, backup)
        print(f"\nbacked up {filled_count} filled row(s) -> {backup.name}")

    write_sheet(out_path, comments, rows)
    print(f"wrote {out_path} ({len(rows)} rows)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--max", type=int, default=100, dest="max_rows",
                    help="cap on total sheet rows; above this, new rows are subsampled")
    ap.add_argument("--seed", type=int, default=20260819,
                    help="RNG seed for the subsample; recorded in the sheet header")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.candidates.exists():
        raise SystemExit(f"no candidates file at {args.candidates}")
    return build(args.candidates, args.out, args.max_rows, args.seed, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
