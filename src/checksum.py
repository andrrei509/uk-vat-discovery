"""
UK VAT registration number (VRN) format + check-digit validation.

Why this module exists
----------------------
Every candidate VAT number we scrape off a web page costs an HMRC API call to
verify. HMRC rate-limits us. The check digit is a free filter: a random
9-digit string passes it roughly 1 time in 97, so validating locally throws
away ~99% of garbage before we spend a single request.

That ratio is worth stating in the writeup, and it is also the whole answer to
debate topic #1 (enumeration): the checksum is not much of a filter when you
are *generating* numbers, because 1-in-97 of 10^7 bases is still ~10^5... work
that out yourself, don't take my word for it.

The algorithm
-------------
A standard VRN is 9 digits: 7 "base" digits + 2 "check" digits.

  weighted = 8*d1 + 7*d2 + 6*d3 + 5*d4 + 4*d5 + 3*d6 + 2*d7

Two schemes are in circulation:

  "97"   (pre-2009 allocations):  (weighted + check) mod 97 == 0
  "9755" (post-2009 allocations): (weighted + check + 55) mod 97 == 0

A number is accepted if EITHER holds. Note the consequence, and say it out
loud in your writeup: accepting both schemes roughly *doubles* the false-pass
rate versus a single scheme (~2 in 97 instead of ~1 in 97). You cannot tell
from the number alone which scheme applies, so this is a real precision cost
you are choosing to pay in exchange for not silently dropping valid numbers.

Other shapes you will meet in real text:
  - 12 digits  = 9-digit VRN + 3-digit branch/subsidiary suffix
  - GD000-GD499 = government departments
  - HA500-HA999 = NHS health authorities
  - "GB" prefix, spaces, dots, non-breaking spaces, en-dashes

Run `python src/checksum.py` to execute the self-tests at the bottom.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Literal, Optional

WEIGHTS = (8, 7, 6, 5, 4, 3, 2)

Scheme = Literal["97", "9755", "government", "health"]


@dataclass(frozen=True)
class VatNumber:
    """A syntactically valid UK VAT number."""

    normalised: str  # canonical form, no spaces, no GB prefix, e.g. "220430231"
    scheme: Scheme
    branch: Optional[str] = None  # 3-digit suffix if the 12-digit form was used

    def __str__(self) -> str:
        return self.normalised


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

# Characters that appear as separators inside VAT numbers on real web pages.
_SEPARATORS = re.compile(r"[\s   .\-‐‑‒–—/]+")


def normalise(raw: str) -> str:
    """Strip separators and an optional GB/XI prefix. Upper-case the result."""
    s = _SEPARATORS.sub("", raw).upper()
    if s.startswith("GB") or s.startswith("XI"):
        s = s[2:]
    return s


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def _weighted_sum(base: str) -> int:
    return sum(w * int(d) for w, d in zip(WEIGHTS, base))


def validate(raw: str) -> Optional[VatNumber]:
    """
    Return a VatNumber if `raw` is a syntactically valid UK VRN, else None.

    Syntactic validity says nothing about whether the number is *issued* — only
    HMRC can tell you that — and nothing at all about who it belongs to.
    Keep those three ideas separate everywhere in this project:
        well-formed  <  issued (HMRC says yes)  <  belongs to company X
    """
    s = normalise(raw)

    # Government departments and health authorities: no check digit at all.
    if re.fullmatch(r"GD[0-4]\d\d", s):
        return VatNumber(normalised=s, scheme="government")
    if re.fullmatch(r"HA[5-9]\d\d", s):
        return VatNumber(normalised=s, scheme="health")

    if not re.fullmatch(r"\d{9}(\d{3})?", s):
        return None

    branch = s[9:] if len(s) == 12 else None
    core = s[:9]
    base, check = core[:7], int(core[7:9])

    # A VRN whose base is all zeros is not issued; drop it before it eats an
    # API call. "000000000" turns up constantly in template/placeholder text.
    if base == "0000000":
        return None

    total = _weighted_sum(base)

    if (total + check) % 97 == 0:
        return VatNumber(normalised=core, scheme="97", branch=branch)
    if (total + check + 55) % 97 == 0:
        return VatNumber(normalised=core, scheme="9755", branch=branch)
    return None


def is_valid(raw: str) -> bool:
    return validate(raw) is not None


# --------------------------------------------------------------------------
# Extraction from free text
# --------------------------------------------------------------------------

# Deliberately loose. We want recall here and we let validate() do the
# filtering, because a tight regex silently drops formats you never thought of
# and you will never know it happened.
#
# Two families:
#   (a) an explicit GB/XI prefix, with or without separators
#   (b) a bare 9- or 12-digit run that is NOT part of a longer digit string
#
# Family (b) is where the false positives live: phone numbers, company
# registration numbers, order references, CSS pixel values. That is fine at
# this stage. What matters is that you MEASURE how many survive validate() and
# then how many survive HMRC, and report both.
VAT_PATTERN = re.compile(
    r"""
    (?<![0-9A-Za-z])                 # not glued to another token
    (?:
        (?:GB|XI)\s*[\s.\-]?\s*      # optional country prefix
        (?:\d[\s.\-]?){8}\d          # 9 digits, separators allowed
        (?:[\s.\-]?(?:\d[\s.\-]?){2}\d)?   # optional 3-digit branch
      |
        \d{3}[\s.\-]\d{4}[\s.\-]\d{2}      # the common "123 4567 89" grouping
      |
        \d{9}(?:\d{3})?              # bare run
    )
    (?![0-9A-Za-z])
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Words that, when they sit near a digit run, make it far more likely to be a
# VAT number. Used for *scoring* candidates, never for filtering them out —
# plenty of real numbers appear with no label at all.
CONTEXT_TERMS = (
    "vat",
    "v.a.t",
    "vat no",
    "vat number",
    "vat reg",
    "vat registration",
    "value added tax",
    "tva",  # bilingual sites
    "cod tva",
)


def iter_candidates(text: str, context_window: int = 60) -> Iterator[dict]:
    """
    Yield every candidate VAT number in `text` with its surrounding context.

    Yields dicts:
        raw        : the matched substring, as it appeared
        start,end  : character offsets in `text`
        context    : +/- context_window characters around the match
        has_label  : True if a VAT-ish word appears in the context window
        parsed     : VatNumber if the check digit passes, else None

    Keeping the *rejected* candidates (parsed is None) is the point. The count
    of rejections per source is evidence that the filter is doing work, and
    Part 2 asks you for a rejection breakdown.
    """
    lowered = text.lower()
    for m in VAT_PATTERN.finditer(text):
        lo = max(0, m.start() - context_window)
        hi = min(len(text), m.end() + context_window)
        context = text[lo:hi]
        has_label = any(term in lowered[lo:hi] for term in CONTEXT_TERMS)
        yield {
            "raw": m.group(0),
            "start": m.start(),
            "end": m.end(),
            "context": context.replace("\n", " ").strip(),
            "has_label": has_label,
            "parsed": validate(m.group(0)),
        }


# --------------------------------------------------------------------------
# Self-tests
# --------------------------------------------------------------------------

if __name__ == "__main__":
    import random

    # Real, publicly-published VAT numbers. Verify each of these against HMRC
    # yourself on day 1 and record the raw JSON — that is your first evidence
    # file, and it also proves this module agrees with HMRC.
    KNOWN_GOOD = [
        "GB 220 4302 31",  # grouping + prefix
        "220430231",
        "GB220430231",
        "220 4302 31",
    ]
    for v in KNOWN_GOOD:
        r = validate(v)
        assert r is not None, f"expected valid: {v}"
        assert r.normalised == "220430231", r
        print(f"  ok  {v!r:20} -> {r.normalised} (scheme {r.scheme})")

    assert validate("220430232") is None, "check digit should fail"
    assert validate("000000000") is None, "all-zero base rejected"
    assert validate("12345") is None
    assert validate("GD001").scheme == "government"
    assert validate("HA512").scheme == "health"
    assert validate("220430231001").branch == "001"
    print("  ok  negative cases + GD/HA + branch suffix")

    # Measure the false-pass rate of the checksum on random 9-digit strings.
    # This is a number you should quote in the writeup rather than asserting
    # "the checksum filters most junk".
    random.seed(0)
    n = 200_000
    passes = sum(
        1 for _ in range(n) if is_valid(f"{random.randint(1_000_000, 9_999_999)}{random.randint(0, 99):02d}")
    )
    print(f"\n  random 9-digit strings passing the checksum: {passes}/{n} = {passes / n:.4%}")
    print(f"  i.e. roughly 1 in {n / max(passes, 1):.0f}  (expected ~1 in 48 for two schemes)")

    text = """
        Acme Widgets Ltd, VAT Reg No. GB 220 4302 31.
        Call us on 0207 946 0958. Company registration 04567890.
        Our supplier quotes VAT 220430232 which is a typo.
    """
    print("\n  extraction demo:")
    for c in iter_candidates(text):
        state = c["parsed"].normalised if c["parsed"] else "REJECTED"
        print(f"    {c['raw']!r:20} label={c['has_label']!s:5} -> {state}")
