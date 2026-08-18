"""
HMRC "Check a UK VAT Number" API client.

Design notes you should be able to defend in the technical call
---------------------------------------------------------------
1. Every raw response is written to disk before anything parses it.
   If a number in your writeup cannot be traced back to a file on disk, it is
   not a number you can defend. `data/raw/hmrc/<vrn>.json` is that trail.

2. The client caches on disk and never repeats a lookup.
   HMRC rate-limits. Re-running the pipeline must not cost fresh quota.
   Cache hits are logged so you can honestly say how many *real* calls you made.

3. There are two endpoints and they are not equivalent:

     GET /organisations/vat/check-vat-number/lookup/{vrn}
         -> name, address, processing date

     GET /organisations/vat/check-vat-number/lookup/{vrn}/{requesterVrn}
         -> the above PLUS a `consultationNumber`

   The second is a receipt: HMRC's own record that you performed this check at
   this time. It requires you to supply a requester VRN. Decide whether you use
   it and write down why — it is exactly the kind of small judgement call the
   assignment is watching for.

4. 404 is a *result*, not an error.
   "This VRN is not registered" is information. Cache it like any other answer.

Auth
----
As of writing, HMRC's Developer Hub classifies endpoints as open-access,
application-restricted, or user-restricted. Which one applies here is
something you must confirm from the docs yourself on day 1 — it determines
whether you need production credentials and whether those need approval.

This client supports both:
  - no credentials  -> plain GET (works if the endpoint is open access)
  - client id/secret -> OAuth2 client_credentials, `read:vat` scope

Credentials live in `.env` at the repo root, which is gitignored. `.env.example`
shows the shape and IS committed. Never put a secret in a tracked file — you are
publishing this repo.

    HMRC_ENV=sandbox|production        (default: sandbox)
    HMRC_CLIENT_ID=...
    HMRC_CLIENT_SECRET=...

Usage:
    python src/hmrc_client.py 220430231
    python src/hmrc_client.py --file data/results/candidates.csv --column vat
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
from typing import Optional

import requests

# Load .env if present. Kept dependency-free on purpose: one less thing to
# install on a Windows box, and the parser is four lines.
def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    # utf-8-sig, not utf-8: PowerShell's Set-Content writes a BOM, which would
    # otherwise turn the first key into "﻿HMRC_ENV" and silently ignore it.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from config import USER_AGENT  # noqa: E402  (must follow .env load)

BASE_URLS = {
    "sandbox": "https://test-api.service.hmrc.gov.uk",
    "production": "https://api.service.hmrc.gov.uk",
}

ACCEPT = "application/vnd.hmrc.2.0+json"

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw" / "hmrc"
LOG_PATH = REPO_ROOT / "data" / "raw" / "hmrc_calls.jsonl"


@dataclass
class LookupResult:
    """Outcome of one VRN lookup."""

    vrn: str
    status: str  # "registered" | "not_found" | "invalid_request" | "error"
    http_status: Optional[int] = None
    name: Optional[str] = None
    address: dict = field(default_factory=dict)
    consultation_number: Optional[str] = None
    processing_date: Optional[str] = None
    raw_path: Optional[str] = None
    from_cache: bool = False

    @property
    def found(self) -> bool:
        return self.status == "registered"

    def address_line(self) -> str:
        a = self.address or {}
        parts = [a.get(k) for k in ("line1", "line2", "line3", "line4", "postcode")]
        return ", ".join(p for p in parts if p)


class HmrcClient:
    def __init__(
        self,
        env: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        requester_vrn: Optional[str] = None,
        min_interval: float = 0.6,
        max_retries: int = 5,
        raw_dir: Path = RAW_DIR,
    ):
        self.env = env or os.environ.get("HMRC_ENV", "sandbox")
        if self.env not in BASE_URLS:
            raise ValueError(f"HMRC_ENV must be one of {list(BASE_URLS)}, got {self.env!r}")
        self.base_url = BASE_URLS[self.env]
        self.client_id = client_id or os.environ.get("HMRC_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("HMRC_CLIENT_SECRET")
        self.requester_vrn = requester_vrn or os.environ.get("HMRC_REQUESTER_VRN")

        self.min_interval = min_interval  # seconds between calls; be polite
        self.max_retries = max_retries
        self.raw_dir = raw_dir / self.env
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        self._token: Optional[str] = None
        self._token_expiry: float = 0.0
        self._last_call: float = 0.0

        self.stats = {"calls": 0, "cache_hits": 0, "retries": 0, "errors": 0}

        self.session = requests.Session()
        # Identify yourself. Costs nothing, and "I set a real User-Agent with
        # contact details" is a sentence worth being able to write.
        self.session.headers.update(
            {
                "Accept": ACCEPT,
                "User-Agent": USER_AGENT,
            }
        )

    # ---------------------------------------------------------------- auth

    def _bearer(self) -> Optional[str]:
        """Return a valid access token, or None if running without credentials."""
        if not (self.client_id and self.client_secret):
            return None
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        resp = self.session.post(
            f"{self.base_url}/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "read:vat",
            },
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expiry = time.time() + int(payload.get("expires_in", 14400))
        return self._token

    # --------------------------------------------------------------- cache

    def _cache_path(self, vrn: str) -> Path:
        return self.raw_dir / f"{vrn}.json"

    # -------------------------------------------------------------- lookup

    def lookup(self, vrn: str, use_consultation: Optional[bool] = None) -> LookupResult:
        """
        Look up one VRN. Returns a LookupResult; never raises for 404.

        `use_consultation` defaults to True when a requester VRN is configured.
        """
        vrn = vrn.strip().upper().replace(" ", "")
        if vrn.startswith("GB"):
            vrn = vrn[2:]

        cache = self._cache_path(vrn)
        if cache.exists():
            self.stats["cache_hits"] += 1
            return self._parse(vrn, json.loads(cache.read_text()), from_cache=True)

        if use_consultation is None:
            use_consultation = bool(self.requester_vrn)

        path = f"/organisations/vat/check-vat-number/lookup/{vrn}"
        if use_consultation:
            if not self.requester_vrn:
                raise ValueError("use_consultation=True requires HMRC_REQUESTER_VRN")
            path += f"/{self.requester_vrn}"

        headers = {}
        token = self._bearer()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        envelope = self._request_with_backoff(path, headers, vrn)
        cache.write_text(json.dumps(envelope, indent=2, sort_keys=True))
        return self._parse(vrn, envelope, from_cache=False)

    def _request_with_backoff(self, path: str, headers: dict, vrn: str) -> dict:
        url = self.base_url + path
        delay = 1.0
        for attempt in range(self.max_retries):
            # Simple client-side pacing.
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)

            resp = self.session.get(url, headers=headers, timeout=45)
            self._last_call = time.time()
            self.stats["calls"] += 1

            envelope = {
                "vrn": vrn,
                "url": url,
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "http_status": resp.status_code,
                "attempt": attempt + 1,
                "env": self.env,
            }
            try:
                envelope["body"] = resp.json()
            except ValueError:
                envelope["body"] = {"_raw_text": resp.text[:4000]}

            self._log(envelope)

            # 429 = rate limited, 5xx = transient. Back off and retry.
            if resp.status_code == 429 or resp.status_code >= 500:
                self.stats["retries"] += 1
                retry_after = resp.headers.get("Retry-After")
                sleep_for = float(retry_after) if retry_after else delay * (1 + random.random())
                print(
                    f"    [{resp.status_code}] backing off {sleep_for:.1f}s "
                    f"(attempt {attempt + 1}/{self.max_retries})",
                    file=sys.stderr,
                )
                time.sleep(sleep_for)
                delay = min(delay * 2, 60)
                continue

            return envelope

        self.stats["errors"] += 1
        envelope["exhausted_retries"] = True
        return envelope

    def _log(self, envelope: dict) -> None:
        with LOG_PATH.open("a") as fh:
            fh.write(
                json.dumps(
                    {
                        k: envelope[k]
                        for k in ("vrn", "requested_at", "http_status", "attempt", "env")
                        if k in envelope
                    }
                )
                + "\n"
            )

    def _parse(self, vrn: str, envelope: dict, from_cache: bool) -> LookupResult:
        http_status = envelope.get("http_status")
        body = envelope.get("body") or {}
        raw_path = str(self._cache_path(vrn).relative_to(REPO_ROOT))

        if http_status == 200:
            target = body.get("target", body)
            return LookupResult(
                vrn=vrn,
                status="registered",
                http_status=200,
                name=target.get("name"),
                address=target.get("address", {}) or {},
                consultation_number=body.get("consultationNumber"),
                processing_date=body.get("processingDate"),
                raw_path=raw_path,
                from_cache=from_cache,
            )
        if http_status == 404:
            return LookupResult(vrn=vrn, status="not_found", http_status=404,
                                raw_path=raw_path, from_cache=from_cache)
        if http_status == 400:
            return LookupResult(vrn=vrn, status="invalid_request", http_status=400,
                                raw_path=raw_path, from_cache=from_cache)
        return LookupResult(vrn=vrn, status="error", http_status=http_status,
                            raw_path=raw_path, from_cache=from_cache)

    def report(self) -> str:
        s = self.stats
        return (
            f"HMRC calls: {s['calls']} live, {s['cache_hits']} cached, "
            f"{s['retries']} retries, {s['errors']} exhausted"
        )


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="HMRC VAT number lookup")
    ap.add_argument("vrn", nargs="*", help="one or more VAT numbers")
    ap.add_argument("--file", help="CSV of candidates")
    ap.add_argument("--column", default="vat", help="column holding the VAT number")
    ap.add_argument("--env", choices=list(BASE_URLS), default=None)
    ap.add_argument("--no-consultation", action="store_true")
    args = ap.parse_args()

    vrns = list(args.vrn)
    if args.file:
        import csv

        with open(args.file, newline="") as fh:
            vrns += [row[args.column] for row in csv.DictReader(fh) if row.get(args.column)]

    if not vrns:
        ap.error("give at least one VRN or --file")

    client = HmrcClient(env=args.env)
    print(f"env={client.env}  authenticated={bool(client.client_id)}  n={len(vrns)}\n")

    for vrn in vrns:
        r = client.lookup(vrn, use_consultation=False if args.no_consultation else None)
        tag = "cache" if r.from_cache else "live "
        if r.found:
            print(f"[{tag}] {r.vrn}  {r.status:12}  {r.name}")
            print(f"          {r.address_line()}")
            if r.consultation_number:
                print(f"          consultation: {r.consultation_number}")
        else:
            print(f"[{tag}] {r.vrn}  {r.status:12}  (HTTP {r.http_status})")

    print(f"\n{client.report()}")
    print(f"raw responses -> {client.raw_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
