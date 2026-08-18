# UK VAT Identifier Discovery

<!--
  SKELETON ONLY. Every heading below is a slot you fill with your own words.
  The bracketed notes tell you what has to be in that section and why — they
  come from the assignment text and from what the grader said they look for.
  Delete each note once you've written the section.

  Rule for the whole document: if a number appears here, a file in this repo
  produced it, and you can rerun the command that made it.
-->

**Candidate:** Andrei-Marian Dulce
**Role:** Data Assets Intern, Veridion
**Submitted:** 2026-08-24

## TL;DR

<!-- Five sentences, written LAST. What you set out to test, the single most
     important thing you found, the headline numbers, and the honest verdict on
     whether this dataset can be built. -->

## How to reproduce

<!-- Clean-clone instructions. Someone must be able to run this. -->

```bash
pip install -r requirements.txt
export HMRC_CLIENT_ID=... HMRC_CLIENT_SECRET=... HMRC_ENV=production
python src/sample.py --seed 42          # redraws the frozen sample
python src/verify.py                    # HMRC verification pass
python src/metrics.py                   # the three numbers
```

---

# Part 1 — Research

## 1.1 What the problem actually is

<!-- Restate the problem more precisely than the brief did. In particular:
     - what function you need (company -> VAT), and what direction the only
       verifier runs in
     - the three asymmetries: unknown denominator, asymmetric+invisible error
       cost, and "not found" being two different outcomes
     If you can state these better than they did, you have already signalled
     you understood it. -->

## 1.2 How many UK companies could even have a VAT number?

<!-- Your denominator estimate, with assumptions written out. This is the
     section almost nobody writes and it changes the meaning of every coverage
     number you report later. Show the arithmetic. Give a RANGE, not a point.
     State what would sharpen it. -->

## 1.3 Source landscape

<!-- One subsection per source you actually touched. For each:
       - what you expected before you looked
       - what you ran (command / URL / n)
       - what came back (the number)
       - verdict: usable / partially usable / dead end
     Depth should be UNEVEN. Some sources deserve four lines, one deserves two
     pages. Uniform depth across sources is the tell that nobody really
     investigated anything. -->

### 1.3.1 <source name>

## 1.4 Dead ends

<!-- The section they said is worth more than the scraper. For each dead end:
     source, expectation, exact reason it failed, and the evidence.
     "Scraping is unreliable" is not a dead end. A statute that says the field
     you hoped for isn't required, plus a count of how many publishers include
     it anyway, is. -->

## 1.5 What I learned that wasn't obvious at the start

<!-- Literally the Part 1 question. Answer it in your own voice. -->

---

# Part 2 — Proof of concept

## 2.1 Sampling design

<!-- How the sample was drawn, the seed, why it resembles the customer's
     supplier base, and — this is the part that earns credit — the specific
     ways it is NOT representative. Name your own sample's weaknesses before
     they do. The frozen sample is at data/sample/sample.csv. -->

## 2.2 Pipeline

<!-- What runs, in what order, with what filters. A diagram in text is fine.
     Say which stage throws away the most candidates and why. -->

## 2.3 Verification: validity is not ownership

<!-- HMRC confirming a number is real does NOT mean it belongs to the company
     you attached it to. Explain your name/address agreement test, and be
     honest about where it is weak (trading names, formation-agent addresses,
     fuzzy matching). Quantify your matcher's own error rate. -->

## 2.4 Results

<!-- Three numbers, not one:
       1. coverage — found/sample AND found/estimated-VAT-registered-in-sample
       2. precision — hand-audited, as a fraction (47/50), with a CI
       3. rejection breakdown — how many candidates died at each stage and why
     Round numbers read as invented. Fractions read as measured. -->

## 2.5 What these numbers don't capture

<!-- Explicitly requested by the brief. Selection effects, sectors untouched,
     the fact that false negatives are unmeasurable here. -->

---

# Part 3 — What I'd do with real resources

<!-- "I'd use a distributed crawler" was quoted as the anti-example. Required:
       - the architecture, and why it changes shape at scale
       - rough cost per company acquired, with the arithmetic
       - what breaks FIRST, named specifically
       - what you'd monitor in production: concrete metrics and alert
         thresholds, not "a data quality dashboard"
       - how coverage and accuracy actually change, with numbers -->

---

# Debate topics

## Enumerating the checksum space against HMRC's checker

<!-- There is a technical answer and an ethical answer and they are different.
     Give both. Include the arithmetic on how many candidates actually survive
     the checksum. -->

## Keeping the dataset current

## Knowing you're wrong at scale with nothing to compare against

## Sources I would not ship

<!-- Name at least one and say what coverage you'd lose. "All of them are
     fine" is the weak answer. -->

---

# Beyond the UK

<!-- Optional. One country compared properly beats a table of 27.
     If you're short on time, DELETE this section rather than write it thin. -->

---

# Appendix

- `DECISIONS.md` — dated decision log kept during the week
- `data/raw/hmrc/` — every raw HMRC response, one JSON per VRN
- `audit/manual_audit.csv` — the hand-checked pairs behind the precision number
- `src/sources/` — one module per source tried, **including the ones that failed**
