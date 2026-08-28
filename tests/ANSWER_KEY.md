# Reconciliation Test Set — Answer Key

30 ledger records + 34 bank records = 64 total records.
Use this to score your agent's actual match rate against the *intended* match rate —
if your numbers don't line up with this key, that tells you exactly where your matching
logic is weak.

Expected outcome: **26 clean/near-clean matches, 4 split/combined matches (multi-row),
and 8 genuine exceptions** (3 ledger-side, 5 bank-side).

## 1. Clean matches (should be easy — sanity check your pipeline works at all)
| Ledger | Bank | Note |
|---|---|---|
| L004 | B2004 | exact amount/date, name in different case/format |
| L010 | B2010 | exact match |
| L012 | B2011 | exact match |
| L019 | B2021 | exact match |
| L022 | B2024 | exact match |
| L024 | B2026 | exact match |
| L026 | B2028 | exact match |
| L028 | B2030 | exact match |

## 2. Name-format variation (fuzzy matching required, but amount/date agree)
| Ledger | Bank | Note |
|---|---|---|
| L001 | B2001 | "Acme Vendors" vs "ACME VENDORS PVT LTD" + date +1 day |
| L002 | B2002 | "Swiggy" vs "SWIGGY BANGALORE" |
| L027 | B2029 | same Acme naming variant, exact amount/date |
| L013 | B2012 | bank narration has leading whitespace — tests trimming, not just fuzzy name logic |
| L015 | B2015 | "Acme Vendors" vs "ACMEVEND TRF REF9284" — deliberately hard; a payment-processor-style
  name with no obvious string overlap. This is the case to check whether your agent
  over-relies on name similarity vs. amount+date+process-of-elimination. |

## 3. Date offset (within a "reasonable" window — decide your own threshold, e.g. ±5 days)
| Ledger | Bank | Offset |
|---|---|---|
| L001 | B2001 | +1 day |
| L025 | B2027 | +1 day |
| L017 | B2019 | +4 days (crosses a weekend) |
| L018 | B2020 | **+16 days** — deliberately outside a normal clearing window. Decide whether
  your agent should still match this (amount/currency/name all agree) or flag it as a
  low-confidence match / exception due to the date gap. There's no universally "correct"
  answer here — this is meant to force you to define and document a threshold. |

## 4. Amount mismatch
| Ledger | Bank | Note |
|---|---|---|
| L005 | B2005 | 150000.00 vs 149950.00 — bank deducted a ₹50 processing fee. Small, explainable. |
| L023 | B2025 | 15000.00 vs 15000.50 — trivial rounding, should still match with a flagged discrepancy |
| L003 | B2003 | 75000.00 vs 74500.00 (₹500 gap) **+ date offset of 3 days** — stacked discrepancies,
  ambiguous. Should this match with low confidence, or go to exceptions? Your call — but
  make sure your agent doesn't silently auto-match this one at high confidence. |

## 5. Currency mismatch (should be flagged as anomaly, not silently matched)
| Ledger | Bank | Note |
|---|---|---|
| L006 | B2006 | Ledger says USD 4500, bank record shows INR 4500, same merchant (Amazon). This is a
  data entry error in the ledger — flag it, don't just match on amount+name and ignore
  currency. |

## 6. Split / combined transactions (1:many and many:1 — the hardest category)
| Ledger | Bank rows | Note |
|---|---|---|
| L007 + L008 | B2007 | Two ₹2,000 Ola Cabs ledger entries same day → bank shows one combined
  ₹4,000 settlement. Many-ledger-to-one-bank. |
| L009 | B2008 + B2009 | One ₹60,000 Zenith Traders ledger entry → bank shows two payments
  (₹40,000 + ₹20,000) on different days. One-ledger-to-many-bank. |
| L016 | B2016 + B2017 + B2018 | ₹220,000 Titan Constructions milestone → paid in three bank
  tranches (₹100k + ₹70k + ₹50k) over 3 days. Tests whether your agent can sum multiple
  candidates to reach a target amount. |

If your agent only does 1:1 matching, these three cases should show up as your biggest
gap versus this answer key — that's the point.

## 7. Genuine ledger-side exceptions (in ledger, no bank counterpart — 3 total)
| Ledger | Reason |
|---|---|
| L011 | Payment recorded but not yet cleared/settled (pending in transit) |
| L020 | Charge was reversed by the merchant before it ever hit the bank; ledger was never
  updated to reflect the cancellation |
| L029 | Petty Cash / cash transaction — never touches the bank account at all. Arguably this
  shouldn't even be in the reconciliation universe; decide whether your agent should
  auto-exclude cash-tagged rows or still report them as an exception. |

## 8. Genuine bank-side exceptions (in bank, no ledger counterpart — 5 total)
| Bank | Reason |
|---|---|
| B2014 | Duplicate Swiggy charge one day after the real one (B2013) — likely a duplicate
  authorization that wasn't reversed cleanly, needs investigation |
| B2023 | A refund/reversal of L021's payment (B2022) — the original matches fine, but the
  refund itself was never recorded in the ledger |
| B2032 | Bank service charge — routine fee, never makes it into the internal ledger |
| B2033 | Interest credit — routine bank-side income, never logged internally |
| B2034 | "WIRE TRF UNKNOWN ORIGIN" — ₹55,000 with no plausible ledger counterpart at all.
  This is the one that should read as *genuinely unresolved* in your output, not just
  "missing paperwork" like the fee/interest rows. |

## Suggested match-rate math
- Total ledger records: 30
- Ledger records with a resolvable bank counterpart (clean + fuzzy + split): 27
  (30 minus L011, L020, L029)
- Your "match rate" for the report should be something like:
  `resolved_ledger_records / total_ledger_records` = 27/30 = 90%
- Then separately report: "3 ledger exceptions, 5 bank-side exceptions, with reasons" —
  this is the honest exception list the track is asking for. Don't fold bank-only
  exceptions into your denominator in a way that inflates the headline number.

## Why this matters for your scoring
The track explicitly says "one cherry-picked match proves nothing." This set is built so
that a naive exact-match-on-amount-and-date join gets maybe 8-10 matches right and calls
everything else an exception (a low, honest-but-weak score). A well-built agent with
fuzzy name matching, a sane date tolerance window, and split/combine detection should
get close to the 27/30 above. The gap between those two numbers is basically a rubric
for how good your matching logic is.
