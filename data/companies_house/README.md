# Companies House snapshot goes here

Download `BasicCompanyDataAsOneFile-YYYY-MM-01.zip` (~470 MB) from
<https://download.companieshouse.gov.uk/en_output.html> into this folder.

The zip and any extracted CSV/parquet are gitignored — they are far too large to
commit. Instead, record in `DECISIONS.md`:

- the exact filename (it carries the snapshot date)
- its SHA256 (`python src/companies_house.py --hash`)
- the row count

That is enough for someone to reproduce the run without the bulk file.

Then:

    python src/companies_house.py --to-parquet    # once; ~10x faster afterwards
    python src/companies_house.py --profile
