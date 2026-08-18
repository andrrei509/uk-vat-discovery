# HMRC API — facts gathered from the docs

Raw facts and citations only. Read them and draw your own conclusions in
`README.md` / `DECISIONS.md` — nothing here is an argument.

Retrieved 18 Aug 2026 from the HMRC Developer Hub.

---

## Check a UK VAT Number API v2.0

<https://developer.service.hmrc.gov.uk/api-documentation/docs/api/service/vat-registered-companies-api/2.0>

- Version 2.0 — beta. Last updated 01 April 2026.
- Sandbox base URL `https://test-api.service.hmrc.gov.uk`
- Production base URL `https://api.service.hmrc.gov.uk`
- **Version 1 was removed on 17 February 2025.** It was open; v2 is not.
- Stated purpose, verbatim: *"for the sole purpose of allowing traders to do due
  diligence on VAT-registered businesses."*
- Stated reason for adding authentication, verbatim: *"We moved the API behind
  authentication so we can fully understand our users."*

### Production access — quoted verbatim

> "Registering should take around 2 weeks. It may take longer if we need more
> information."
>
> "You will get production credentials once you have tested in the Sandbox
> environment and accepted the Terms of Use 2.0."

Submission deadline is 24 Aug 2026. Application created 18 Aug 2026.

### Endpoints (both `applicationRestricted`, `read:vat` scope)

```
GET /organisations/vat/check-vat-number/lookup/{targetVrn}
GET /organisations/vat/check-vat-number/lookup/{targetVrn}/{requesterVrn}
```

Headers: `Accept: application/vnd.hmrc.2.0+json`, `Authorization: Bearer <token>`

`targetVrn`: 9-digit or 12-digit. Responses documented: 200, 400, 404, 500
(and 403 on the two-VRN form).

200 response shape:

```json
{
  "target": {
    "name": "Credite Sberger Donal Inc.",
    "vatNumber": "553557881",
    "address": { }
  },
  "requester": "146295999727",
  "consultationNumber": "ypAeKRPlW",
  "processingDate": "2019-01-31T12:53:05+00:00"
}
```

`requester` and `consultationNumber` appear only on the two-VRN form.

### Sandbox test data

40 mock VRNs, mirrored to `data/reference/hmrc_sandbox_test_vrns.csv`.
Source: <https://github.com/hmrc/vat-registered-companies-api/tree/main/public/api/conf/2.0/test-data>
(22 are 9-digit, 18 are 12-digit.)

**Measured:** only **1 of the 40** passes the mod-97/mod-9755 check digit —
`726129090` (9755 scheme). HMRC's own documentation example, `553557881`, fails
it too. Reproduce with:

```powershell
python -c "import sys,csv; sys.path.insert(0,'src'); from checksum import validate; rows=[r['vrn'] for r in csv.DictReader(open('data/reference/hmrc_sandbox_test_vrns.csv'))]; print(sum(1 for r in rows if validate(r)), '/', len(rows))"
```

### Observed on the application dashboard

The Developer Hub application page shows **"Last API call"** per application,
alongside application name, ID and environment.

---

## Check an EORI Number API v1.0

<https://developer.service.hmrc.gov.uk/api-documentation/docs/api/service/check-eori-number-api/1.0/oas/page>

Listed by HMRC under *"APIs with only open access endpoints — You do not need an
application or subscription to use open access endpoints."*

The OpenAPI spec states, verbatim: **`AUTHORIZATIONS: None`**

### Endpoint

```
POST /customs/eori/lookup/check-multiple-eori
Content-Type: application/json

{"eoris": ["GB123456789133", "GB123456789136", "GB8392848394939"]}
```

- **1 to 10 EORIs per request.** More returns 400.
- Each item 14–17 characters, must match `^(GB)[0-9]{12,15}$`.
- A 400 rejects the **entire batch**, not the offending item.

Response:

```json
[
  {"eori": "GB123456789133", "valid": true,
   "companyDetails": { }, "processingDate": "2021-01-05T09:54:08+00:00"},
  {"eori": "GB123456729136", "valid": true,
   "processingDate": "2021-01-05T09:54:08+00:00"},
  {"eori": "GB8392848394939", "valid": false,
   "processingDate": "2021-01-05T09:54:08+00:00"}
]
```

### Documented limitations, verbatim

> "view the name and address of the business that the EORI number is registered
> to **(if the business agreed to share this information)**."

> "Invalid payload - one or more EORI numbers begin with XI. To check an EORI
> number that starts with XI, use the EORI checker service on the European
> Commission website"

That second one is HMRC itself confirming the post-Brexit GB/XI split.

### Sandbox stubbing (by last digit of the EORI)

| last digit | behaviour |
|---|---|
| 0, 1 | valid, no trader name or address |
| 2–5 | valid, **with** trader name and address |
| 6, 7 | valid, no trader name or address |
| 8, 9 | invalid |

---

## The identifier relationship

`GB EORI = "GB" + <9-digit VRN> + <3-digit suffix>`, suffix usually `000`.

`GB` + 9 + 3 = 14 characters, which sits inside HMRC's own
`^(GB)[0-9]{12,15}$` and inside the documented 14–17 character bound.

**Untested as of writing.** `src/eori_client.py` implements it. Test it against
a VAT number you have independently confirmed before relying on it, and record
the result either way.

Things to measure rather than assume:

1. Does `GB<known-good-VRN>000` return `valid: true`?
2. What share of `valid: true` responses include `companyDetails`? That share
   bounds how much ownership testing this route can do.
3. What happens for a VRN belonging to a business that does not trade goods
   internationally, and therefore should hold no EORI?
4. Do branch suffixes (`001`, `002`, …) matter in practice, or is `000` enough?
