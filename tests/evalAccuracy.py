"""
eval_harness.py

Runs ReconciliationEngine against the known-answer test CSVs and scores its
output against ground truth. This is what turns "trust me, it works" into an
actual number — run this before every demo/submission to know your real
match rate, not just whatever number the engine's own summary reports (the
whole point is to check those agree).

Usage:
    python -m tests.evalAccuracy
    python -m tests.evalAccuracy.py --bank tests/bank_statement.csv --ledger tests/ledger.csv
    python -m tests.evalAccuracy.py --report-out eval_report.json
"""

import os
import sys
import json
import argparse
import django 
from jobs.services import ReconciliationEngine
from django.conf import settings

# ---------------------------------------------------------------------------
# Django bootstrapping
# ---------------------------------------------------------------------------
# engine.py does `from django.conf import settings` and reads settings.TESTING
# inside its LLM call — so Django needs to be set up before we can import it.
# Adjust DJANGO_SETTINGS_MODULE below to match your actual project package
# name (the folder containing settings.py — same one manage.py points at).

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "AI_transaction_processing_pipeline.settings")

django.setup()

# ! Force TESTING mode for the whole harness run. This matters for two reasons:
# ! 1. LLM mock response is fixed and static
# ! 2. i can't afford that much API request rate :(
settings.TESTING = True
BANK_FILE = "tests/bank_statement.csv"
LEDGER_FILE = "tests/ledger.csv"
EXPECTED_MATCHES = {
    # Clean matches
    "L004": ["B2004"],
    "L010": ["B2010"],
    "L012": ["B2011"],
    "L019": ["B2021"],
    "L022": ["B2024"],
    "L024": ["B2026"],
    "L026": ["B2028"],
    "L028": ["B2030"],

    # Name-format variation
    "L001": ["B2001"],
    "L002": ["B2002"],
    "L027": ["B2029"],
    "L013": ["B2012"],
    "L015": ["B2015"],

    # Date offset
    "L025": ["B2027"],
    "L017": ["B2019"],
    "L018": ["B2020"],

    # Amount mismatch
    "L005": ["B2005"],
    "L023": ["B2025"],
    "L003": ["B2003"],

    # Currency mismatch
    "L006": ["B2006"],

    # Split / combined
    "L007": ["B2007"],
    "L008": ["B2007"],
    "L009": ["B2008", "B2009"],
    "L016": ["B2016", "B2017", "B2018"],
    "L014": ["B2013"],
    "L021": ["B2022"],
    "L030": ["B2031"],
}

LEDGER_EXCEPTIONS = {
    "L011",  
    "L020",  
    "L029",  
}

BANK_EXCEPTIONS = {
    "B2014",  
    "B2023",
    "B2032",  
    "B2033",  
    "B2034",  
}


def build_actual_match_map(matches):
    """
    Turn the engine's `matches` list into {ledger_id: set(bank_refs)}.
    Handles both shapes the engine can emit:
      - normal/split match: {"ledger_id": "L001", "bank_refs": [...]}
      - combine match:      {"ledger_id": ["L007", "L008"], "bank_refs": [...]}
    In the combine case, every ledger_id in the list maps to the same
    bank_refs set, since e.g. both L007 and L008 were jointly settled by B2007.
    """
    actual = {}
    for m in matches:
        lid = m["ledger_id"]
        bank_refs = set(m["bank_refs"])
        if isinstance(lid, list):
            for single_lid in lid:
                actual[single_lid] = bank_refs
        else:
            actual[lid] = bank_refs
    return actual


def evaluate(result):
    matches = result["matches"]
    ledger_exceptions_actual = {e["ledger_id"] for e in result["ledger_exceptions"]}
    bank_exceptions_actual = {e["bank_ref"] for e in result["bank_exceptions"]}
    actual_match_map = build_actual_match_map(matches)

    exact_matches = []          # engine matched exactly the expected bank_refs
    partial_matches = []        # engine matched, but to the wrong bank_ref(s)
    missed_matches = []         # engine should have matched this but didn't

    for ledger_id, expected_refs in EXPECTED_MATCHES.items():
        expected_set = set(expected_refs)

        if ledger_id in actual_match_map:
            actual_set = actual_match_map[ledger_id]
            if actual_set == expected_set:
                exact_matches.append(ledger_id)
            else:
                partial_matches.append((ledger_id, expected_set, actual_set))
        elif ledger_id in ledger_exceptions_actual:
            missed_matches.append((ledger_id, expected_set, "flagged as exception instead of matched"))
        else:
            missed_matches.append((ledger_id, expected_set, "missing from output entirely"))

    # (a) force-match something thatshould have stayed an exception, or 
    # (b) exception-out something that should have matched? 
    # (c) is already captured above via missed_matches.
    ledger_exc_correct = LEDGER_EXCEPTIONS & ledger_exceptions_actual
    ledger_exc_false_match = LEDGER_EXCEPTIONS - ledger_exceptions_actual  # should've been exception, got force-matched
    ledger_exc_unexpected = ledger_exceptions_actual - LEDGER_EXCEPTIONS   # flagged exception, shouldn't have been

    bank_exc_correct = BANK_EXCEPTIONS & bank_exceptions_actual
    bank_exc_false_match = BANK_EXCEPTIONS - bank_exceptions_actual        # should've been exception, got force-matched
    bank_exc_unexpected = bank_exceptions_actual - BANK_EXCEPTIONS         # flagged exception, shouldn't have been

    total_ledger_cases = len(EXPECTED_MATCHES)+len(LEDGER_EXCEPTIONS)
    reported_match_rate = result["summary"]["match_rate_percent"]
    true_match_rate = round(100 * len(exact_matches) / total_ledger_cases, 1)

    return {
        "exact_matches": exact_matches,
        "partial_matches": partial_matches,
        "missed_matches": missed_matches,
        "ledger_exceptions": {
            "correct": sorted(ledger_exc_correct),
            "false_matched": sorted(ledger_exc_false_match),
            "unexpectedly_flagged": sorted(ledger_exc_unexpected),
        },
        "bank_exceptions": {
            "correct": sorted(bank_exc_correct),
            "false_matched": sorted(bank_exc_false_match),
            "unexpectedly_flagged": sorted(bank_exc_unexpected),
        },
        "reported_match_rate_percent": reported_match_rate,
        "true_match_rate_percent": true_match_rate,
        "exact_match_count": len(exact_matches),
        "partial_match_count": len(partial_matches),
        "missed_match_count": len(missed_matches),
        "total_expected_matches": total_ledger_cases,
    }

def print_report(scores):
    print("=" * 70)
    print("RECONCILIATION ENGINE — EVAL HARNESS REPORT")
    print("=" * 70)

    print(f"\nEngine-reported match rate:  {scores['reported_match_rate_percent']}%")
    print(f"Ground-truth match rate:     {scores['true_match_rate_percent']}%  "
          f"({scores['exact_match_count']}/{scores['total_expected_matches']} exact)")

    if scores["reported_match_rate_percent"] != scores["true_match_rate_percent"]:
        print("  ⚠ These numbers disagree — your engine's own summary math and its "
              "actual accuracy have drifted apart. Investigate before trusting the "
              "headline number in a demo.")

    print(f"\nExact matches:   {scores['exact_match_count']}")
    print(f"Partial matches: {scores['partial_match_count']}  (matched, but to the wrong bank record)")
    print(f"Missed matches:  {scores['missed_match_count']}  (should have matched, didn't)")

    if scores["partial_matches"]:
        print("\n--- Partial matches (wrong pairing) ---")
        for ledger_id, expected, actual in scores["partial_matches"]:
            print(f"  {ledger_id}: expected {sorted(expected)}, got {sorted(actual)}")

    if scores["missed_matches"]:
        print("\n--- Missed matches ---")
        for ledger_id, expected, reason in scores["missed_matches"]:
            print(f"  {ledger_id}: expected {sorted(expected)} — {reason}")

    le = scores["ledger_exceptions"]
    print(f"\nLedger exceptions correctly identified: {len(le['correct'])}/{len(LEDGER_EXCEPTIONS)}")
    if le["false_matched"]:
        print(f"  ⚠ Should have been exceptions but got force-matched: {le['false_matched']}")
    if le["unexpectedly_flagged"]:
        print(f"  ⚠ Flagged as exceptions but shouldn't have been: {le['unexpectedly_flagged']}")

    be = scores["bank_exceptions"]
    print(f"\nBank exceptions correctly identified: {len(be['correct'])}/{len(BANK_EXCEPTIONS)}")
    if be["false_matched"]:
        print(f"  ⚠ Should have been exceptions but got force-matched: {be['false_matched']}")
    if be["unexpectedly_flagged"]:
        print(f"  ⚠ Flagged as exceptions but shouldn't have been: {be['unexpectedly_flagged']}")

    print("\n" + "=" * 70)

def main():
    parser = argparse.ArgumentParser(description="Score the reconciliation engine against ground truth.")
    parser.add_argument("--bank", default=BANK_FILE, help="Path to bank statement CSV")
    parser.add_argument("--ledger", default=LEDGER_FILE, help="Path to ledger CSV")
    parser.add_argument("--report-out", default=None, help="Optional path to write a JSON report")
    args = parser.parse_args()

    engine = ReconciliationEngine()

    with open(args.bank, "rb") as bank_fh, open(args.ledger, "rb") as ledger_fh:
        result = engine.process(bank_file=bank_fh, ledger_file=ledger_fh)

    scores = evaluate(result)
    print_report(scores)

    if args.report_out:
        with open(args.report_out, "w") as f:
            # sets aren't JSON-serializable, so make sure everything in scores
            # is already a plain list/dict (evaluate() returns sorted lists,
            # so this should be safe as-is)
            json.dump({"engine_result_summary": result["summary"], "scores": scores}, f, indent=2, default=str)
        print(f"\nFull report written to {args.report_out}")

    # Non-zero exit code on real regressions (partial/missed matches) — lets
    # you wire this into CI as a pass/fail gate, not just a printout you have
    # to read manually.
    if scores["partial_match_count"] > 0 or scores["missed_match_count"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()