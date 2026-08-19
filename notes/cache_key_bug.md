# The HMRC cache-key bug, and the second one it caused

Factual record, for the decision log. No argument here — just what happened, in
order, with the evidence each step produced.

---

## The original bug

`HmrcClient._cache_path()` was:

```python
def _cache_path(self, vrn: str) -> Path:
    return self.raw_dir / f"{vrn}.json"
```

One file per VAT number. But the same VAT number returns genuinely different
responses depending on how it is asked:

| Request | Response |
|---|---|
| no bearer token | `401` — the endpoint is application-restricted |
| valid bearer token | `404` — this VRN is not in the sandbox stub data |

Both were written to `data/raw/hmrc/sandbox/220430231.json`. The 401 came first
(18 Aug, 15:15 UTC), the 404 five minutes later (15:20 UTC), and the 404
overwrote the 401.

**What that cost.** `CLAUDE.md` claimed:

> Both raw responses (401 and 404) are in `data/raw/hmrc/sandbox/`.

That was false at the time it was written. Only the 404 survived. The 401 existed
only as one line in `data/raw/hmrc_calls.jsonl`, which records status and
timestamp but no response body:

```json
{"vrn": "220430231", "requested_at": "2026-08-18T15:15:39.859276+00:00",
 "http_status": 401, "attempt": 1, "env": "sandbox"}
```

The lost one was the more useful of the two: the 401 is what proves the endpoint
is application-restricted, and that claim is load-bearing for the whole
verification strategy.

It was also not re-runnable. Credentials worked by then, so any fresh call for
that VRN returned 404. Recovering the 401 needed a deliberate no-credentials
request, and no way to make one existed.

---

## The fix

Key the cache on the whole request rather than on the VAT number:

```
data/raw/hmrc/<env>/<vrn>.<auth|noauth>.<single|consult>.json
```

- `env` stays the directory (unchanged).
- `auth`/`noauth` — whether a bearer token was sent.
- `single`/`consult` — the one-VRN endpoint, or the two-VRN form that returns a
  `consultationNumber`. Different response shapes, so they must not share a file.

Auth mode is decided from configuration with no network call (`auth_mode()`),
because the cache path has to be known before deciding whether to make a request
at all.

Added `--no-auth`, which sends no bearer token even when credentials are
configured. That is what makes the 401 reproducible on demand instead of only by
accident before setup.

Each envelope now also records `auth_mode`, `authenticated` and
`endpoint_variant`, so an evidence file is interpretable without relying on its
own filename.

---

## The second bug, introduced by the fix

The fix included a backwards-compatibility path so pre-existing `{vrn}.json`
files would still be read rather than silently triggering fresh live calls:

```python
if not cache.exists() and self._legacy_cache_path(vrn).exists():
    cache = self._legacy_cache_path(vrn)
```

That reads the legacy file for **any** mode. So the first run of
`--no-auth` was handed the authenticated **404** out of cache and never made the
call at all:

```
env=sandbox  auth_mode=noauth  n=1
[cache] 220430231  not_found     (HTTP 404)
```

This is the same bug as the original, from the other direction. The first version
let an authenticated response destroy an unauthenticated one on write. This
version served an authenticated response in place of an unauthenticated one on
read. Both come from the same root cause: a filename that cannot distinguish two
different requests.

**Fix for that.** A legacy file may only answer a request whose mode matches.
Legacy files carry no `auth_mode` field, so the mode is inferred from the status
code, which is unambiguous for this endpoint — `401` means no usable token was
sent, anything else means one was:

```python
implied = "noauth" if envelope.get("http_status") == 401 else "auth"
return implied == mode
```

That is `_legacy_usable()` in `src/hmrc_client.py`.

---

## The evidence now on disk

Captured 19 Aug 2026, 21:30:31 UTC,
`python src/hmrc_client.py 220430231 --no-consultation --no-auth`, written to
`data/raw/hmrc/sandbox/220430231.noauth.single.json`:

```json
{
  "auth_mode": "noauth",
  "authenticated": false,
  "body": {
    "code": "MISSING_CREDENTIALS",
    "message": "Authentication information is not provided"
  },
  "endpoint_variant": "single",
  "env": "sandbox",
  "http_status": 401,
  "url": "https://test-api.service.hmrc.gov.uk/organisations/vat/check-vat-number/lookup/220430231",
  "vrn": "220430231"
}
```

The response body names the failure mode:
**`MISSING_CREDENTIALS` — "Authentication information is not provided"**. That is
stronger than a bare status code, because it is HMRC stating the reason rather
than us inferring it from a number.

The 404 for the same VRN is unchanged at
`data/raw/hmrc/sandbox/220430231.json`, and is still served for `auth` mode via
the compatibility path.

## Verified from a clean clone

Fresh `git clone`, fresh virtualenv, `pip install -r requirements.txt`, **no
`.env` present**:

```
$ python src/hmrc_client.py 220430231 --no-consultation --no-auth
env=sandbox  auth_mode=noauth  n=1
[cache] 220430231  error         (HTTP 401)
          raw: data\raw\hmrc\sandbox\220430231.noauth.single.json
```

Zero live calls, no credentials, no network. With credentials present the same
command reports `auth_mode=auth` and serves the 404 instead. One command, two
modes, two committed responses, neither able to overwrite the other.
