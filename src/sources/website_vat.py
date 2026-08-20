"""
Source module: VAT numbers published on company websites.

This is Path C (targeted crawl) in miniature, and right now it has a narrower
job: produce enough REAL VAT numbers to measure what share of EORI lookups
return trader details.

⚠️  READ THIS BEFORE YOU USE THE OUTPUT FOR ANYTHING ELSE  ⚠️

Whatever domain list you point this at first will be a CONVENIENCE SAMPLE —
companies you picked because you expected them to publish a VAT number. The
brief calls that out by name:

    "a sample of companies you already knew published their VAT number will
     produce an impressive number and teach neither of us anything"

That makes it useless for measuring COVERAGE, and fine for characterising the
VERIFIER, because the EORI opt-in rate is a property of HMRC's service, not of
your sampling. Keep the two uses separate, keep this output in
`data/results/probe_*.csv`, and never let it touch `data/sample/sample.csv`.

Even for the verifier measurement there is a bias worth stating: firms that
publish a VAT number prominently skew large and international, so they are more
likely to hold an EORI at all, and may well differ in whether they opted into
sharing details. Say so rather than hoping nobody notices.

Politeness
----------
- robots.txt is fetched and obeyed per host, before anything else.
- One request at a time, with a configurable delay.
- A real User-Agent carrying a contact address.
- Pages are cached to disk, so re-running costs the site nothing.

Being able to write "I respected robots.txt, rate-limited to one request every
N seconds, and identified myself" answers "would you ship this?" before they
ask it.

Usage:
    python src/sources/website_vat.py --domains data/results/sample_domains.csv \
                                      --match-strength strong \
                                      --out data/results/candidates.csv
    python src/eori_client.py --file data/results/candidates.csv --column vat

Run `weak` separately and never into the same file — see `--match-strength`:

    python src/sources/website_vat.py --domains data/results/sample_domains.csv \
                                      --match-strength weak \
                                      --out data/results/candidates_weak.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import time
import urllib.robotparser
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from checksum import iter_candidates  # noqa: E402
from config import USER_AGENT  # noqa: E402

PAGE_CACHE = REPO_ROOT / "data" / "raw" / "pages"

# Where VAT numbers actually live. Ordered by hit probability — legal/company
# info pages beat the homepage, because publishing the number is a compliance
# act and compliance text clusters.
CANDIDATE_PATHS = [
    "/",
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/terms",
    "/terms-and-conditions",
    "/terms-of-service",
    "/privacy",
    "/privacy-policy",
    "/legal",
    "/imprint",
    "/company-information",
    "/delivery",
    "/returns",
]

_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")


def html_to_text(html: str) -> str:
    """Crude but dependency-free. Good enough: we want digits and nearby words."""
    text = _TAG.sub(" ", html)
    text = _ANY_TAG.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&#160;", " ")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    return _WS.sub(" ", text)


@dataclass
class Candidate:
    # First, so every row can be joined straight back to data/sample/sample.csv.
    # Blank when the input was the legacy two-column format, which carries no
    # company number — and a candidate with no join key is much less useful,
    # which is the reason for preferring sample_domains.csv as input.
    company_number: str
    company: str
    domain: str
    url: str
    raw_match: str
    vat: str
    scheme: str
    has_label: bool
    context: str
    page_sha256: str


FIELDNAMES = ["company_number", "company", "domain", "url", "raw_match", "vat",
              "scheme", "has_label", "context", "page_sha256"]


class PoliteFetcher:
    def __init__(self, delay: float = 2.0, timeout: int = 20, user_agent: Optional[str] = None):
        self.delay = delay
        self.timeout = timeout
        self.ua = user_agent or USER_AGENT
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.ua})
        self._robots: dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
        self._last = 0.0
        PAGE_CACHE.mkdir(parents=True, exist_ok=True)
        self.stats = {"fetched": 0, "cached": 0, "blocked": 0, "errors": 0, "not_found": 0}

    def _robots_for(self, base: str):
        host = urlparse(base).netloc
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            url = urljoin(base, "/robots.txt")
            rp.set_url(url)
            try:
                # Fetched through the session rather than via rp.read(), which
                # calls urllib with NO timeout and will hang indefinitely on a
                # host that accepts the connection and then stalls. Harmless
                # against a handful of known domains; a guaranteed hang when
                # pointed at hundreds of speculative ones (see
                # src/domain_discovery.py).
                resp = self.session.get(url, timeout=self.timeout,
                                        allow_redirects=True)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    # 404 / 403 / anything else: no usable rules.
                    rp = None
            except Exception:
                # Unreachable. Convention is that this means "allowed" — but
                # record it, don't silently assume.
                rp = None
            self._robots[host] = rp
        return self._robots[host]

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        if rp is None:
            return True
        return rp.can_fetch(self.ua, url)

    def get(self, url: str) -> Optional[tuple[str, str]]:
        """Return (text, sha256) or None. Caches raw HTML to disk."""
        key = hashlib.sha256(url.encode()).hexdigest()[:24]
        cached = PAGE_CACHE / f"{key}.html"
        if cached.exists():
            self.stats["cached"] += 1
            html = cached.read_text(encoding="utf-8", errors="replace")
            return html_to_text(html), hashlib.sha256(html.encode()).hexdigest()

        if not self.allowed(url):
            self.stats["blocked"] += 1
            print(f"    robots.txt disallows {url}", file=sys.stderr)
            return None

        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as exc:
            self.stats["errors"] += 1
            print(f"    {type(exc).__name__} on {url}", file=sys.stderr)
            return None
        finally:
            self._last = time.time()

        if resp.status_code == 404:
            self.stats["not_found"] += 1
            return None
        if resp.status_code != 200 or "html" not in resp.headers.get("Content-Type", ""):
            self.stats["errors"] += 1
            return None

        self.stats["fetched"] += 1
        cached.write_text(resp.text, encoding="utf-8", errors="replace")
        return html_to_text(resp.text), hashlib.sha256(resp.text.encode()).hexdigest()


def scan_domain(fetcher: PoliteFetcher, company: str, domain: str,
                paths: Iterable[str] = CANDIDATE_PATHS,
                company_number: str = "") -> list[Candidate]:
    base = domain if domain.startswith("http") else f"https://{domain}"
    found: dict[str, Candidate] = {}

    for path in paths:
        url = urljoin(base, path)
        got = fetcher.get(url)
        if not got:
            continue
        text, sha = got
        for c in iter_candidates(text):
            parsed = c["parsed"]
            if not parsed:
                continue  # rejected by the check digit; counted by metrics later
            vrn = parsed.normalised
            # Keep the first sighting, but prefer one that had a VAT label
            # nearby — better provenance for the same number.
            if vrn not in found or (c["has_label"] and not found[vrn].has_label):
                found[vrn] = Candidate(
                    company_number=company_number,
                    company=company,
                    domain=urlparse(base).netloc,
                    url=url,
                    raw_match=c["raw"],
                    vat=vrn,
                    scheme=parsed.scheme,
                    has_label=c["has_label"],
                    context=c["context"][:300],
                    page_sha256=sha,
                )
    return list(found.values())


def load_domain_rows(path: str, match_strength: str, limit=None) -> list[dict]:
    """
    Accepts either format:

      A) the simple two-column list:   company,domain
      B) the output of domain_discovery.py:
         company_number,company_name,candidate_domain,...,match_strength,...

    For (B), rows are filtered by match_strength. Default is 'strong' only.

    Why 'strong' only by default: a 'weak' match means the domain resolved and
    the page contained the company's distinctive word — but that word is what
    GENERATED the domain in the first place, so the test is close to circular.
    Any unrelated firm sharing the word scores weak. Crawling those and counting
    what comes back would inflate coverage with numbers belonging to other
    companies. Run them separately with --match-strength weak if you want to
    demonstrate that, but keep the output in its own file.
    """
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8-sig")))
    if not rows:
        return []

    if "candidate_domain" in rows[0]:
        before = len(rows)
        rows = [r for r in rows
                if (r.get("match_strength") or "").strip().lower() == match_strength
                and (r.get("candidate_domain") or "").strip()]
        print(f"discovery format: kept {len(rows)}/{before} rows "
              f"with match_strength={match_strength!r}")
        rows = [{"company": r["company_name"],
                 "domain": r["candidate_domain"],
                 "company_number": r.get("company_number", "")} for r in rows]
    else:
        rows = [{"company": r["company"], "domain": r["domain"],
                 "company_number": r.get("company_number", "")} for r in rows]

    return rows[:limit] if limit else rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domains", required=True,
                    help="CSV: either company,domain or the domain_discovery.py output")
    ap.add_argument("--out", required=True)
    ap.add_argument("--match-strength", default="strong",
                    choices=["strong", "weak", "none"],
                    help="only used for domain_discovery.py input (default: strong)")
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    rows = load_domain_rows(args.domains, args.match_strength, args.limit)
    if not rows:
        print("No domains to scan. Check the file and --match-strength.")
        return 1

    fetcher = PoliteFetcher(delay=args.delay)
    all_candidates: list[Candidate] = []
    with_any = 0

    for i, row in enumerate(rows, 1):
        company, domain = row["company"].strip(), row["domain"].strip()
        print(f"[{i}/{len(rows)}] {company}  ({domain})")
        # Passed down rather than looked up afterwards by company name: two
        # companies can share a name, and the join key must not depend on the
        # name being unique.
        cands = scan_domain(fetcher, company, domain,
                            company_number=(row.get("company_number") or "").strip())
        if cands:
            with_any += 1
            for c in cands:
                label = "labelled" if c.has_label else "UNLABELLED"
                print(f"      {c.vat}  {label}  {urlparse(c.url).path or '/'}")
        all_candidates.extend(cands)

    fields = FIELDNAMES
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for c in all_candidates:
            d = asdict(c)
            w.writerow({k: d.get(k, "") for k in fields})

    print("\n" + "=" * 60)
    print(f"match_strength           : {args.match_strength}")
    print(f"domains scanned          : {len(rows)}")
    print(f"domains yielding >=1 VRN : {with_any}/{len(rows)}"
          f"  ({with_any / max(len(rows), 1):.1%})")
    print(f"distinct (company, vrn)  : {len(all_candidates)}")
    labelled = sum(1 for c in all_candidates if c.has_label)
    print(f"of which VAT-labelled    : {labelled}/{len(all_candidates)}"
          f"  <- unlabelled ones are the risky ones")
    print(f"pages: {fetcher.stats}")
    print(f"\nwrote {out}")
    print(f"\nNext: python src/eori_client.py --file {args.out} --column vat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
