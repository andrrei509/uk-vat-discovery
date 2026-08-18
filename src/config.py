"""
Single source of truth for how this project identifies itself over the network.

Every outbound request — HMRC VAT, HMRC EORI, and any website fetch — sends the
same User-Agent. That matters for two reasons:

1. A site operator or HMRC looking at their logs sees one coherent identity,
   not three near-identical strings they can't correlate.
2. "I identified myself consistently and gave a contact route" is a sentence you
   want to be able to write in the writeup, and it should be true.

Convention note: the `+` prefix belongs in front of a URL, not an email. That's
where it comes from in crawler User-Agents (`+https://example.com/bot`), and it
tells the reader "this is where to find out who I am". Once the repo is public,
put the GitHub URL in VAT_PROJECT_URL and the string becomes genuinely useful to
someone trying to work out who just hit their server.

Override any of these with environment variables rather than editing the file,
so a clean clone still runs.
"""

from __future__ import annotations

import os

VERSION = "0.1"

# Change this, or set VAT_CONTACT in your environment.
CONTACT = os.environ.get("VAT_CONTACT", "andreimarian.dulce@gmail.com")

# Set once the repo is public. An empty value is simply omitted.
PROJECT_URL = os.environ.get("VAT_PROJECT_URL", "")


def _build() -> str:
    parts = [f"uk-vat-discovery/{VERSION}"]
    detail = ["research prototype"]
    if PROJECT_URL:
        detail.append(f"+{PROJECT_URL}")
    detail.append(f"contact: {CONTACT}")
    parts.append("(" + "; ".join(detail) + ")")
    return " ".join(parts)


USER_AGENT = os.environ.get("VAT_USER_AGENT") or _build()


if __name__ == "__main__":
    print(USER_AGENT)
