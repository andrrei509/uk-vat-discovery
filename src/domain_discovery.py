"""
Guess company websites from company names, and measure how often the guess is
wrong.

The point is the error rate, not the domains
--------------------------------------------
Companies House ships no website field, so every "crawl the company's site"
strategy has an unpriced prerequisite: mapping 500 registered names onto 500
domains. This module implements the cheapest possible version of that mapping —
strip the legal form off the name, collapse to alphanumerics, try three TLDs —
and then spends most of its effort on the harder question: **when a domain
resolves, is it actually that company's?**

A resolving domain is not evidence. `smithbuilders.co.uk` resolves whether or
not it belongs to the Smith Builders Ltd in the sample. So every hit is scored:

    strong   page contains the company number, or the full registered name
             (core + a legal-form word, so "Daisy Tufts Ltd" counts)
    weak     page contains only the distinctive part of the name
    none     resolves, but nothing matches -> NOT FOUND, row kept
    no_domain  no candidate resolved at all

`weak` is deliberately separated from `strong` because the distinctive part is
what generated the domain in the first place, so a weak match is close to
circular: any unrelated business sharing that word scores it. The 20-row
hand-check sample exists to put a number on how often that happens.

Politeness, and one deliberate deviation
----------------------------------------
Reuses `PoliteFetcher` from `sources/website_vat.py`: same User-Agent with a
contact address, same robots.txt handling, same on-disk page cache.

The deviation: `PoliteFetcher` paces requests globally, one every `delay`
seconds no matter who they go to. That is right when hitting a handful of hosts
repeatedly, and wrong here. This module touches ~1500 *distinct* hosts and sends
each of them at most two requests (robots.txt, then the root). Load on any one
host is what politeness is about, so pacing is per-host, with a small global
floor so the run is never a burst source. Both are configurable and printed at
the start of every run.

The run therefore has two phases, and only the second is paced:

  1. **Resolve.** Every candidate name is looked up, concurrently. Most do not
     exist, and a failed lookup is slow (AAAA then A, seconds before giving up);
     serially that measured ~3.6 s per candidate, which is hours for this
     sample. DNS queries go to a resolver, not to the company's server, so
     parallelising them loads nobody.
  2. **Fetch.** Only names that resolved get an HTTP request, strictly serial
     and paced. Non-resolving candidates are recorded as `DNSFailure` without
     any connection attempt.

Resumable, because the run is long and mostly failures
------------------------------------------------------
Every attempt — including DNS failures, timeouts and robots blocks — is appended
to `data/raw/domain_attempts.jsonl` as it happens and skipped on re-run. Killing
the process loses nothing. That matters more than usual here: most candidates do
not exist, and a non-resumable run would re-pay for discovering that.

Usage
-----
    python src/domain_discovery.py --limit 20        # try it small first
    python src/domain_discovery.py                   # the full sample
    python src/domain_discovery.py --report-only     # re-print from the cache
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "src" / "sources"))

from website_vat import PoliteFetcher, html_to_text, PAGE_CACHE  # noqa: E402

SAMPLE = REPO_ROOT / "data" / "sample" / "sample.csv"
OUT_CSV = REPO_ROOT / "data" / "results" / "sample_domains.csv"
REPORT = REPO_ROOT / "notes" / "domain_discovery_output.txt"
ATTEMPTS = REPO_ROOT / "data" / "raw" / "domain_attempts.jsonl"

SEED = 20260820
N_HANDCHECK = 20

COLUMNS = ["company_number", "company_name", "candidate_domain", "http_status",
           "match_strength", "matched_on", "page_sha256", "notes"]

TLDS = ["co.uk", "com", "uk"]

# Legal-form and article words only. Deliberately conservative: stripping
# "GROUP"/"SERVICES"/"HOLDINGS" too would turn a specific name into a generic
# one and manufacture domains like `services.co.uk`, guaranteeing false matches
# and corrupting the very error rate this module exists to measure.
STRIP_WORDS = {
    "LIMITED", "LTD", "PLC", "LLP", "LP", "THE",
    "CIC", "CIO", "UNLIMITED", "INCORPORATED", "INC",
}
LEGAL_FORMS = ("limited", "ltd", "plc", "llp")

# Cheap markers for domain-parking / for-sale interstitials. Not used to score;
# recorded in `notes` so the hand-check knows what it is looking at.
PARKED_MARKERS = (
    "this domain is for sale", "domain for sale", "buy this domain",
    "parked domain", "domain parking", "godaddy.com/domainsearch",
    "the domain you are looking for", "sedo.com", "hugedomains",
    "under construction", "coming soon", "default web page",
    "welcome to nginx", "apache2 ubuntu default page", "iis windows server",
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_PUNCT = re.compile(r"[^A-Za-z0-9]+")


def collapse(s: str) -> str:
    """Lowercase, alphanumerics only. Used for substring comparisons."""
    return _NON_ALNUM.sub("", (s or "").lower())


def name_words(name: str) -> list[str]:
    """Registered name -> significant words, legal forms removed."""
    # CH writes trailing articles as "(THE)" and separators as commas, so
    # punctuation becomes whitespace before any word-level work.
    words = [w for w in _PUNCT.sub(" ", (name or "").upper()).split() if w]
    # Returns empty rather than falling back to the stripped words. A name like
    # "! LTD" has no distinctive part, and falling back to ["LTD"] would both
    # generate `ltd.co.uk` and make the core "ltd" — which appears on nearly
    # every UK company page, so every hit would score a weak match.
    return [w for w in words if w not in STRIP_WORDS]


def candidate_domains(name: str) -> list[str]:
    """
    Joined and hyphenated forms of the core name, across three TLDs.

    Order matters: it decides which domain a company is credited with when more
    than one resolves, and .co.uk leads because these are UK registrations.
    """
    words = name_words(name)
    joined = "".join(collapse(w) for w in words)
    hyphen = "-".join(collapse(w) for w in words if collapse(w))
    if len(joined) < 3:
        return []
    forms = [joined] if hyphen == joined else [joined, hyphen]
    return [f"{f}.{tld}" for f in forms for tld in TLDS]


def label_near(text: str, digits: str, window: int = 60) -> bool:
    """
    Is an unpadded company number next to something calling it one?

    `393793` on its own is a plausible coincidence — a price, a phone fragment,
    a product code. Requiring a nearby label is what makes the unpadded form
    usable as evidence instead of noise.
    """
    low = text.lower()
    labels = ("company number", "company no", "company reg", "registered number",
              "registered no", "reg no", "reg. no", "companies house",
              "registered in england", "registered in scotland")
    for m in re.finditer(re.escape(digits), low):
        left = low[max(0, m.start() - window):m.start()]
        if any(lb in left for lb in labels):
            return True
    return False


def score(page_text: str, company_number: str, company_name: str) -> tuple[str, str]:
    """
    (match_strength, matched_on) for a page that resolved.

    Comparisons run on both the raw text (for the number, where digit adjacency
    matters) and an alphanumeric-collapsed copy (for names, so "J. Smith &
    Sons" matches "JSmithSons"). Collapsing can join across word boundaries and
    create matches that a human would not accept — which is a known cost of the
    cheap method, and part of what the hand-check measures.
    """
    collapsed = collapse(page_text)
    padded = (company_number or "").strip().upper()
    unpadded = padded.lstrip("0")

    if padded and padded in page_text:
        return "strong", f"company_number:{padded}"
    if unpadded and unpadded != padded and label_near(page_text, unpadded):
        return "strong", f"company_number_unpadded_labelled:{unpadded}"

    full = collapse(company_name)
    if full and full in collapsed:
        return "strong", "full_registered_name"

    core = "".join(collapse(w) for w in name_words(company_name))
    if core:
        for lf in LEGAL_FORMS:
            if core + lf in collapsed:
                return "strong", f"name_plus_legal_form:{lf}"

    if core and len(core) >= 4 and core in collapsed:
        return "weak", "distinctive_name_only"

    return "none", ""


def resolves(domain: str) -> bool:
    try:
        socket.getaddrinfo(domain, 443, proto=socket.IPPROTO_TCP)
        return True
    except OSError:
        return False


def resolve_many(domains: list[str], workers: int = 16) -> dict[str, bool]:
    """
    Resolve every candidate up front, concurrently.

    Most guessed domains do not exist, and a failed lookup is slow — getaddrinfo
    tries AAAA then A and can sit for seconds before giving up. Done serially
    that dominates the whole run; measured at roughly 3.6 s per candidate, which
    is hours for this sample.

    Running them in parallel is not a politeness compromise. A DNS query goes to
    a resolver, not to the company's web server, and resolving a name that does
    not exist puts load on nobody. The HTTP requests that follow stay strictly
    serial and paced. Splitting the two is what makes the run finish without
    ever hitting a host faster than the delay allows.
    """
    from concurrent.futures import ThreadPoolExecutor

    if not domains:
        return {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(domains, pool.map(resolves, domains)))


def parked_note(page_text: str) -> str:
    low = page_text.lower()
    hits = [m for m in PARKED_MARKERS if m in low]
    return f"possible placeholder page ({hits[0]})" if hits else ""


class DomainProber(PoliteFetcher):
    """
    PoliteFetcher plus what this job needs: the HTTP status (the base class
    returns None for everything non-200, which cannot be reported), and per-host
    pacing instead of one global clock.
    """

    def __init__(self, delay: float = 2.0, global_floor: float = 0.3, **kw):
        super().__init__(delay=delay, **kw)
        self.global_floor = global_floor
        self._host_last: dict[str, float] = {}

    def _pace(self, host: str) -> None:
        now = time.time()
        waits = [self.global_floor - (now - self._last)]
        if host in self._host_last:
            waits.append(self.delay - (now - self._host_last[host]))
        wait = max(waits)
        if wait > 0:
            time.sleep(wait)

    def probe(self, domain: str) -> dict:
        """One attempt against one domain's root. Never raises."""
        url = f"https://{domain}/"
        out = {"domain": domain, "url": url, "http_status": None,
               "page_sha256": "", "text": "", "note": ""}

        try:
            allowed = self.allowed(url)
        except Exception as exc:  # robots fetch itself misbehaved
            out["note"] = f"robots check failed: {type(exc).__name__}"
            allowed = True
        if not allowed:
            self.stats["blocked"] += 1
            out["note"] = "robots.txt disallows /"
            out["http_status"] = "robots_blocked"
            return out

        self._pace(urlparse(url).netloc)
        try:
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        except Exception as exc:
            self.stats["errors"] += 1
            out["http_status"] = type(exc).__name__
            out["note"] = "did not resolve or connect"
            return out
        finally:
            self._last = time.time()
            self._host_last[urlparse(url).netloc] = self._last

        out["http_status"] = resp.status_code
        self.stats["fetched"] += 1
        if resp.status_code != 200:
            return out
        if "html" not in resp.headers.get("Content-Type", "").lower():
            out["note"] = f"non-html content-type: {resp.headers.get('Content-Type', '')[:40]}"
            return out

        html = resp.text
        out["page_sha256"] = hashlib.sha256(html.encode("utf-8", "replace")).hexdigest()
        out["text"] = html_to_text(html)
        # Same page cache the other module uses, so nothing is fetched twice.
        try:
            key = hashlib.sha256(url.encode()).hexdigest()[:24]
            (PAGE_CACHE / f"{key}.html").write_text(html, encoding="utf-8", errors="replace")
        except OSError:
            pass
        # A redirect off the guessed host is worth knowing about by itself.
        final = urlparse(resp.url).netloc.lower()
        if final and final.replace("www.", "") != domain.replace("www.", ""):
            out["note"] = (out["note"] + "; " if out["note"] else "") + f"redirected to {final}"
        return out


# --------------------------------------------------------------------------
# attempt cache


def load_attempts() -> dict[tuple[str, str], dict]:
    if not ATTEMPTS.exists():
        return {}
    out = {}
    with ATTEMPTS.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            out[(rec["company_number"], rec["candidate_domain"])] = rec
    return out


def append_attempt(rec: dict) -> None:
    ATTEMPTS.parent.mkdir(parents=True, exist_ok=True)
    with ATTEMPTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
        fh.flush()


# --------------------------------------------------------------------------


def load_sample() -> list[dict]:
    lines = [ln for ln in SAMPLE.read_text(encoding="utf-8-sig").splitlines(keepends=True)
             if not ln.lstrip().startswith("#")]
    return [dict(r) for r in csv.DictReader(lines)]


STRENGTH_ORDER = ["strong", "weak", "none", "no_domain"]


def best_row(company: dict, attempts: list[dict]) -> dict:
    """
    One row per company: the best-scoring attempt, ties broken by candidate order.

    `attempts` is already in candidate order, so `min` on the strength rank is
    stable and the earlier (higher-priority) domain wins a tie.
    """
    resolved = [a for a in attempts if a.get("match_strength") in ("strong", "weak", "none")]
    if not resolved:
        tried = len(attempts)
        notes = []
        if tried == 0:
            notes.append("no candidate domains generated from the name")
        else:
            statuses = sorted({str(a.get("http_status")) for a in attempts})
            notes.append(f"{tried} candidate(s) tried, none served an HTML page")
            notes.append("statuses: " + ",".join(statuses))
        return {
            "company_number": company["company_number"],
            "company_name": company["company_name"],
            "candidate_domain": "", "http_status": "",
            "match_strength": "no_domain", "matched_on": "",
            "page_sha256": "", "notes": "; ".join(notes),
        }
    pick = min(resolved, key=lambda a: STRENGTH_ORDER.index(a["match_strength"]))
    others = len(resolved) - 1
    notes = [n for n in [pick.get("note", "")] if n]
    if others:
        notes.append(f"{others} other candidate(s) also served a page")
    return {
        "company_number": company["company_number"],
        "company_name": company["company_name"],
        "candidate_domain": pick["candidate_domain"],
        "http_status": pick["http_status"],
        "match_strength": pick["match_strength"],
        "matched_on": pick.get("matched_on", ""),
        "page_sha256": pick.get("page_sha256", ""),
        "notes": "; ".join(notes),
    }


def crosstab(rows: list[dict], sample_by_num: dict, key: str) -> dict:
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        band = sample_by_num[r["company_number"]].get(key, "?")
        out.setdefault(band, {s: 0 for s in STRENGTH_ORDER})
        out[band][r["match_strength"]] += 1
    return out


def render(rows: list[dict], sample_by_num: dict, stats: dict, seed: int,
           n_handcheck: int) -> str:
    import numpy as np

    buf = io.StringIO()
    w = buf.write
    n = len(rows)
    counts = {s: sum(1 for r in rows if r["match_strength"] == s) for s in STRENGTH_ORDER}

    w("=== domain discovery: outcome of " + str(n) + " companies " + "=" * 22 + "\n")
    w(f"  {'outcome':<12} {'count':>7}  {'share':>8}\n")
    for s in STRENGTH_ORDER:
        w(f"  {s:<12} {counts[s]:>7}  {counts[s] / n:>8.1%}\n")
    w(f"  {'TOTAL':<12} {n:>7}\n")
    w("\n  'none' resolved but matched nothing, so it counts as NOT FOUND.\n")
    w("  Usable = strong only. weak is near-circular: the distinctive word is\n")
    w("  what generated the domain, so any unrelated firm sharing it scores weak.\n")

    for key, title in (("size_band", "size band"), ("sector_group", "sector group")):
        w(f"\n=== by {title} " + "=" * (52 - len(title)) + "\n")
        tab = crosstab(rows, sample_by_num, key)
        w(f"  {'':<22}" + "".join(f"{s:>10}" for s in STRENGTH_ORDER)
          + f"{'total':>8}{'strong%':>9}\n")
        for band in sorted(tab, key=lambda b: -sum(tab[b].values())):
            c = tab[band]
            tot = sum(c.values())
            w(f"  {band:<22}" + "".join(f"{c[s]:>10}" for s in STRENGTH_ORDER)
              + f"{tot:>8}{c['strong'] / tot:>9.1%}\n")

    rng = np.random.default_rng(seed)
    present = [s for s in STRENGTH_ORDER if counts[s] > 0]
    pools = {s: sorted([r for r in rows if r["match_strength"] == s],
                       key=lambda r: r["company_number"]) for s in present}

    # Even split, then hand back anything a small stratum cannot absorb so the
    # total still reaches n_handcheck instead of quietly coming up short.
    per = {s: 0 for s in present}
    remaining = n_handcheck
    while remaining > 0:
        takers = [s for s in present if per[s] < len(pools[s])]
        if not takers:
            break
        for s in takers:
            if remaining == 0:
                break
            per[s] += 1
            remaining -= 1

    picked = []
    for s in present:
        pool, k = pools[s], per[s]
        if k:
            idx = rng.choice(len(pool), size=k, replace=False)
            picked.extend(pool[i] for i in sorted(int(i) for i in idx))

    w(f"\n=== {len(picked)} rows for hand-checking (seed {seed}) " + "=" * 20 + "\n")
    w("  Spread across every outcome present, not proportional, so the rare\n")
    w("  outcomes get looked at too. Verify each by hand and count the wrong ones:\n")
    w("  that count over these rows is the method's own error rate.\n\n")

    for i, r in enumerate(picked, 1):
        s = sample_by_num[r["company_number"]]
        w(f"  [{i:2d}] {r['match_strength'].upper()}  {r['company_number']}  "
          f"{r['company_name']}\n")
        w(f"       domain    : {r['candidate_domain'] or '(none resolved)'}"
          f"   http: {r['http_status']}\n")
        w(f"       matched_on: {r['matched_on'] or '-'}\n")
        w(f"       CH addr   : {s.get('reg_address_line1', '')}, "
          f"{s.get('reg_post_town', '')} {s.get('reg_postcode', '')}\n")
        w(f"       sector/size: {s.get('sector_group', '')} / {s.get('size_band', '')}\n")
        if r["notes"]:
            w(f"       notes     : {r['notes']}\n")
        w("\n")

    # Derived from the attempt cache on disk, not from a process counter.
    # Counters live and die with the run, so `--report-only` used to print
    # zeroes and overwrite the real numbers with them. Anything reported here
    # has to survive being regenerated later, or it is not evidence.
    w("=== every candidate attempt, by outcome " + "=" * 26 + "\n")
    by_status: dict[str, int] = {}
    for a in stats["attempts"]:
        by_status[str(a.get("http_status"))] = by_status.get(str(a.get("http_status")), 0) + 1
    total_attempts = sum(by_status.values())
    w(f"  {len(stats['attempts'])} attempt(s) across {n} companies "
      f"({total_attempts / max(n, 1):.1f} candidate domains each)\n\n")
    for status, k in sorted(by_status.items(), key=lambda kv: -kv[1]):
        w(f"  {status:<24} {k:>6}  {k / total_attempts:>7.1%}\n")
    w("\n  Only the HTTP statuses involved a request to a company's server.\n")
    w("  DNSFailure rows were resolved and dropped without any connection.\n")
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure name->domain guessing")
    ap.add_argument("--sample", type=Path, default=SAMPLE)
    ap.add_argument("--out", type=Path, default=OUT_CSV)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--delay", type=float, default=2.0,
                    help="seconds between requests to the SAME host")
    ap.add_argument("--global-floor", type=float, default=0.3,
                    help="minimum seconds between any two requests")
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--dns-workers", type=int, default=16,
                    help="concurrent DNS lookups; no HTTP load, so safe to raise")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--handcheck", type=int, default=N_HANDCHECK)
    ap.add_argument("--report-only", action="store_true",
                    help="rebuild output from the attempt cache, fetch nothing")
    args = ap.parse_args()

    if not args.sample.exists():
        raise SystemExit(f"no sample at {args.sample}; run python src/sample.py first")

    sample = load_sample()
    if args.limit:
        sample = sample[: args.limit]
    by_num = {r["company_number"]: r for r in sample}

    done = load_attempts()
    print(f"sample        : {len(sample)} companies")
    print(f"attempt cache : {len(done)} previous attempt(s) in {ATTEMPTS.name}")
    if not args.report_only:
        print(f"pacing        : {args.delay}s per host, {args.global_floor}s global floor")

    prober = None if args.report_only else DomainProber(
        delay=args.delay, global_floor=args.global_floor, timeout=args.timeout)

    plan = {c["company_number"]: candidate_domains(c["company_name"]) for c in sample}
    resolved: dict[str, bool] = {}
    if not args.report_only:
        todo = sorted({d for num, ds in plan.items() for d in ds
                       if (num, d) not in done})
        print(f"candidates     : {sum(len(v) for v in plan.values())} "
              f"({len(todo)} not yet attempted)")
        print(f"resolving DNS  : {len(todo)} name(s) on {args.dns_workers} threads ...")
        t0 = time.time()
        resolved = resolve_many(todo, args.dns_workers)
        live = sum(1 for v in resolved.values() if v)
        print(f"                 {live}/{len(todo)} resolve  "
              f"({time.time() - t0:.0f}s); only those get an HTTP request")

    rows = []
    for i, company in enumerate(sample, 1):
        num, name = company["company_number"], company["company_name"]
        cands = plan[num]
        attempts = []
        for domain in cands:
            key = (num, domain)
            if key in done:
                attempts.append(done[key])
                continue
            if args.report_only:
                continue
            if not resolved.get(domain, True):
                rec = {"company_number": num, "company_name": name,
                       "candidate_domain": domain, "http_status": "DNSFailure",
                       "match_strength": "", "matched_on": "", "page_sha256": "",
                       "note": "domain does not resolve",
                       "attempted_at": datetime.now(timezone.utc).isoformat(
                           timespec="seconds")}
                append_attempt(rec)
                done[key] = rec
                attempts.append(rec)
                continue
            got = prober.probe(domain)
            strength, matched_on = (
                score(got["text"], num, name) if got["text"] else ("", ""))
            note = got["note"]
            if got["text"]:
                pn = parked_note(got["text"])
                if pn:
                    note = (note + "; " if note else "") + pn
            rec = {
                "company_number": num, "company_name": name,
                "candidate_domain": domain, "http_status": got["http_status"],
                "match_strength": strength, "matched_on": matched_on,
                "page_sha256": got["page_sha256"], "note": note,
                # Stamped so two runs writing the same key are distinguishable
                # after the fact. Attempts logged before this field was added
                # do not have it.
                "attempted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            append_attempt(rec)
            done[key] = rec
            attempts.append(rec)
            # Nothing beats a strong match, so stop paying for more requests.
            if strength == "strong":
                break
        row = best_row(company, attempts)
        rows.append(row)
        print(f"[{i}/{len(sample)}] {row['match_strength']:<9} "
              f"{row['candidate_domain'] or '-':<38} {name[:40]}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(sorted(rows, key=lambda r: r["company_number"]))

    mine = [done[(num, d)] for num, ds in plan.items() for d in ds
            if (num, d) in done]
    report = render(rows, by_num, {"attempts": mine}, args.seed, args.handcheck)
    print("\n" + report)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(report, encoding="utf-8")
    print(f"wrote {args.out} ({len(rows)} rows)")
    print(f"wrote {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
