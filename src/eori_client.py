"""
HMRC "Check an EORI Number" API client — an UNAUTHENTICATED verifier.

Why this file exists (read before using it)
-------------------------------------------
HMRC's VAT checker (v2.0) sits behind OAuth and their own docs say registering
for production takes "around 2 weeks. It may take longer if we need more
information." Sandbox serves stub data only. So on any timescale shorter than a
fortnight, the official VAT verifier is unavailable for real numbers.

The EORI checker is not. Its OpenAPI spec says:

    AUTHORIZATIONS: None

It is live in production, right now, with no application, no subscription and
no token.

The relationship that makes this useful:

    GB EORI  =  "GB" + <9-digit VRN> + <3-digit suffix, usually 000>

which matches HMRC's own validation regex `^(GB)[0-9]{12,15}$`.

So a candidate VAT number can be checked against a production HMRC endpoint
today by constructing the EORI and asking whether it is valid.

What this verifier can and cannot tell you
------------------------------------------
This is the part to get right, because it is exactly the validity-vs-ownership
trap that sinks most submissions.

  valid: true   -> an EORI with this number exists and is registered to a real
                   trader. Strong evidence the 9-digit core is a live VRN.
  valid: false  -> tells you almost NOTHING about the VAT number. Only
                   businesses that move goods across a customs border hold an
                   EORI at all. A perfectly valid VAT-registered plumber in
                   Leeds has no EORI and returns false.

So this is a **one-way** signal: it can confirm, it cannot refute.
Report it that way. A "false" is not a negative result, it is a missing test,
and conflating the two would understate your coverage and overstate your
precision.

Second limitation, from the spec: `companyDetails` is returned only
"if the business agreed to share this information." So the ownership test —
comparing the returned trader name against Companies House — is only possible
on the subset that opted in. **Measure that opt-in rate and report it.** It
directly bounds how much ownership checking this route can ever do, and it is a
number nobody else will have.

Constraints from the spec
-------------------------
- POST /customs/eori/lookup/check-multiple-eori
- Body: {"eoris": [...]}, **1 to 10 items per request**
- Each item 14-17 chars, matching ^(GB)[0-9]{12,15}$
- XI (Northern Ireland) numbers are rejected outright: HMRC tells you to use
  the European Commission's checker instead. That is a second confirmation of
  the post-Brexit GB/XI split and worth citing in the writeup.
- Sandbox is stubbed by last digit:
      0,1 -> valid, no trader name/address
      2-5 -> valid, WITH trader name and address
      6,7 -> valid, no trader name/address
      8,9 -> invalid
  Useful for proving the parsing works; useless for real data.

Usage:
    python src/eori_client.py 220430231                 # from a VAT number
    python src/eori_client.py --eori GB220430231000
    python src/eori_client.py --file data/results/candidates.csv --column vat
    python src/eori_client.py --env sandbox 220430232   # exercise the stubs
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

import requests

from config import USER_AGENT

BASE_URLS = {
    "sandbox": "https://test-api.service.hmrc.gov.uk",
    "production": "https://api.service.hmrc.gov.uk",
}

ENDPOINT = "/customs/eori/lookup/check-multiple-eori"
MAX_BATCH = 10  # hard limit from the spec

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw" / "eori"
LOG_PATH = REPO_ROOT / "data" / "raw" / "eori_calls.jsonl"


def vrn_to_eori(vrn: str, suffix: str = "000") -> str:
    """
    Construct the GB EORI for a 9-digit VRN.

    The 000 suffix is the common case (head office / no branch). Companies with
    registered branches use 001, 002, ... — so a `valid: false` on 000 does not
    strictly rule out an EORI existing under another suffix. Whether to sweep
    suffixes is a judgement call: it multiplies your request count by however
    many you try, for a rare case. Decide, and write down which you chose.
    """
    v = vrn.strip().upper().replace(" ", "")
    if v.startswith("GB"):
        v = v[2:]
    if len(v) == 12:  # already VRN + branch
        return f"GB{v}"
    if len(v) != 9 or not v.isdigit():
        raise ValueError(f"expected a 9-digit VRN, got {vrn!r}")
    return f"GB{v}{suffix}"


def eori_to_vrn(eori: str) -> Optional[str]:
    """Inverse: pull the 9-digit VRN core out of a GB EORI."""
    e = eori.strip().upper()
    if not e.startswith("GB") or not e[2:].isdigit() or len(e[2:]) < 12:
        return None
    return e[2:11]


@dataclass
class EoriResult:
    eori: str
    valid: bool
    company_name: Optional[str] = None
    address: dict = field(default_factory=dict)
    processing_date: Optional[str] = None
    raw_path: Optional[str] = None
    from_cache: bool = False

    @property
    def vrn(self) -> Optional[str]:
        return eori_to_vrn(self.eori)

    @property
    def shared_details(self) -> bool:
        """
        True if the trader opted into publishing name/address. The share of
        valid results where this is False bounds how much ownership testing
        this source can do — measure it, don't assume it.
        """
        return bool(self.company_name)

    def address_line(self) -> str:
        a = self.address or {}
        parts = [a.get(k) for k in ("streetAndNumber", "city", "postcode", "country")]
        return ", ".join(p for p in parts if p)


class EoriClient:
    def __init__(
        self,
        env: Optional[str] = None,
        min_interval: float = 0.5,
        max_retries: int = 5,
        raw_dir: Path = RAW_DIR,
    ):
        self.env = env or os.environ.get("EORI_ENV", "production")
        if self.env not in BASE_URLS:
            raise ValueError(f"env must be one of {list(BASE_URLS)}, got {self.env!r}")
        self.base_url = BASE_URLS[self.env]
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.raw_dir = raw_dir / self.env
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        self._last_call = 0.0
        self.stats = {"requests": 0, "eoris": 0, "cache_hits": 0, "retries": 0, "errors": 0}

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            }
        )

    # ------------------------------------------------------------- caching

    def _cache_path(self, eori: str) -> Path:
        return self.raw_dir / f"{eori}.json"

    # -------------------------------------------------------------- lookup

    def check(self, eoris: Iterable[str]) -> list[EoriResult]:
        """Check any number of EORIs, batching to the spec's limit of 10."""
        wanted = list(dict.fromkeys(e.strip().upper() for e in eoris))
        results: dict[str, EoriResult] = {}
        pending: list[str] = []

        for e in wanted:
            cache = self._cache_path(e)
            if cache.exists():
                self.stats["cache_hits"] += 1
                results[e] = self._parse(json.loads(cache.read_text()), from_cache=True)
            else:
                pending.append(e)

        for batch in _chunks(pending, MAX_BATCH):
            for record in self._post_batch(batch):
                eori = record.get("eori", "").upper()
                self._cache_path(eori).write_text(json.dumps(record, indent=2, sort_keys=True))
                results[eori] = self._parse(record, from_cache=False)

        return [results[e] for e in wanted if e in results]

    def check_vrns(self, vrns: Iterable[str], suffix: str = "000") -> list[EoriResult]:
        return self.check(vrn_to_eori(v, suffix) for v in vrns)

    def _post_batch(self, batch: list[str]) -> list[dict]:
        url = self.base_url + ENDPOINT
        delay = 1.0
        for attempt in range(self.max_retries):
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)

            resp = self.session.post(url, json={"eoris": batch}, timeout=45)
            self._last_call = time.time()
            self.stats["requests"] += 1
            self.stats["eoris"] += len(batch)

            self._log(
                {
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                    "env": self.env,
                    "n": len(batch),
                    "http_status": resp.status_code,
                    "attempt": attempt + 1,
                }
            )

            if resp.status_code == 429 or resp.status_code >= 500:
                self.stats["retries"] += 1
                retry_after = resp.headers.get("Retry-After")
                sleep_for = float(retry_after) if retry_after else delay * (1 + random.random())
                print(f"    [{resp.status_code}] backing off {sleep_for:.1f}s", file=sys.stderr)
                time.sleep(sleep_for)
                delay = min(delay * 2, 60)
                continue

            if resp.status_code != 200:
                self.stats["errors"] += 1
                # A 400 here is usually a malformed EORI in the batch. HMRC
                # rejects the WHOLE batch, so one bad item costs you nine good
                # ones — validate before sending.
                print(f"    [{resp.status_code}] {resp.text[:300]}", file=sys.stderr)
                return []

            body = resp.json()
            # Stamp provenance onto each record before it hits disk.
            for record in body:
                record["_env"] = self.env
                record["_requested_at"] = datetime.now(timezone.utc).isoformat()
            return body

        self.stats["errors"] += 1
        return []

    def _log(self, entry: dict) -> None:
        with LOG_PATH.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")

    def _parse(self, record: dict, from_cache: bool) -> EoriResult:
        eori = record.get("eori", "").upper()
        details = record.get("companyDetails") or {}
        return EoriResult(
            eori=eori,
            valid=bool(record.get("valid")),
            company_name=details.get("traderName"),
            address=details.get("address", {}) or {},
            processing_date=record.get("processingDate"),
            raw_path=str(self._cache_path(eori).relative_to(REPO_ROOT)),
            from_cache=from_cache,
        )

    def report(self) -> str:
        s = self.stats
        return (
            f"EORI: {s['requests']} requests covering {s['eoris']} numbers, "
            f"{s['cache_hits']} cached, {s['retries']} retries, {s['errors']} errors"
        )


def _chunks(items: list, n: int) -> Iterator[list]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="HMRC EORI checker (no auth required)")
    ap.add_argument("vrn", nargs="*", help="9-digit VAT numbers")
    ap.add_argument("--eori", action="append", default=[], help="full GB EORI(s)")
    ap.add_argument("--file", help="CSV of candidates")
    ap.add_argument("--column", default="vat")
    ap.add_argument("--suffix", default="000", help="EORI branch suffix (default 000)")
    ap.add_argument("--env", choices=list(BASE_URLS), default=None)
    args = ap.parse_args()

    eoris = list(args.eori)
    vrns = list(args.vrn)
    if args.file:
        import csv

        with open(args.file, newline="") as fh:
            vrns += [r[args.column] for r in csv.DictReader(fh) if r.get(args.column)]
    eoris += [vrn_to_eori(v, args.suffix) for v in vrns]

    if not eoris:
        ap.error("give at least one VRN, --eori, or --file")

    client = EoriClient(env=args.env)
    print(f"env={client.env}  auth=none  n={len(eoris)}\n")

    results = client.check(eoris)
    shared = 0
    for r in results:
        tag = "cache" if r.from_cache else "live "
        state = "VALID  " if r.valid else "invalid"
        print(f"[{tag}] {r.eori}  (vrn {r.vrn})  {state}", end="")
        if r.company_name:
            shared += 1
            print(f"  {r.company_name}")
            if r.address_line():
                print(f"          {r.address_line()}")
        else:
            print("  (no shared details)")

    valid = sum(1 for r in results if r.valid)
    print(f"\n{client.report()}")
    print(f"valid: {valid}/{len(results)}")
    if valid:
        print(f"of which shared trader details: {shared}/{valid} "
              f"= {shared / valid:.1%}  <- the ownership-testable share")
    print(f"raw responses -> {client.raw_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
