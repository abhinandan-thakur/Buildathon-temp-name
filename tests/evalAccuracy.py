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
BANK_FILE = "tests/bank_statement_large.csv"
LEDGER_FILE = "tests/ledger_large.csv"
EXPECTED_MATCHES = {
    'L001': ['B0001'],
    'L002': ['B0002'],
    'L003': ['B0003'],
    'L004': ['B0004'],
    'L005': ['B0005'],
    'L006': ['B0006'],
    'L007': ['B0007'],
    'L008': ['B0008'],
    'L009': ['B0009'],
    'L010': ['B0010'],
    'L011': ['B0011'],
    'L012': ['B0012'],
    'L013': ['B0013'],
    'L014': ['B0014'],
    'L015': ['B0015'],
    'L016': ['B0016'],
    'L017': ['B0017'],
    'L018': ['B0018'],
    'L019': ['B0019'],
    'L020': ['B0020'],
    'L021': ['B0021'],
    'L022': ['B0022'],
    'L023': ['B0023'],
    'L024': ['B0024'],
    'L025': ['B0025'],
    'L026': ['B0026'],
    'L027': ['B0027'],
    'L028': ['B0028'],
    'L029': ['B0029'],
    'L030': ['B0030'],
    'L031': ['B0031'],
    'L032': ['B0032'],
    'L033': ['B0033'],
    'L034': ['B0034'],
    'L035': ['B0035'],
    'L036': ['B0036'],
    'L037': ['B0037'],
    'L038': ['B0038'],
    'L039': ['B0039'],
    'L040': ['B0040'],
    'L041': ['B0041'],
    'L042': ['B0042'],
    'L043': ['B0043'],
    'L044': ['B0044'],
    'L045': ['B0045'],
    'L046': ['B0046'],
    'L047': ['B0047'],
    'L048': ['B0048'],
    'L049': ['B0049'],
    'L050': ['B0050'],
    'L051': ['B0051'],
    'L052': ['B0052'],
    'L053': ['B0053'],
    'L054': ['B0054'],
    'L055': ['B0055'],
    'L056': ['B0056'],
    'L057': ['B0057'],
    'L058': ['B0058'],
    'L059': ['B0059'],
    'L060': ['B0060'],
    'L061': ['B0061'],
    'L062': ['B0062'],
    'L063': ['B0063'],
    'L064': ['B0064'],
    'L065': ['B0065'],
    'L066': ['B0066'],
    'L067': ['B0067'],
    'L068': ['B0068'],
    'L069': ['B0069'],
    'L070': ['B0070'],
    'L071': ['B0071'],
    'L072': ['B0072'],
    'L073': ['B0073'],
    'L074': ['B0074'],
    'L075': ['B0075'],
    'L076': ['B0076'],
    'L077': ['B0077'],
    'L078': ['B0078'],
    'L079': ['B0079'],
    'L080': ['B0080'],
    'L081': ['B0081'],
    'L082': ['B0082'],
    'L083': ['B0083'],
    'L084': ['B0084'],
    'L085': ['B0085'],
    'L086': ['B0086'],
    'L087': ['B0087'],
    'L088': ['B0088'],
    'L089': ['B0089'],
    'L090': ['B0090'],
    'L091': ['B0091'],
    'L092': ['B0092'],
    'L093': ['B0093'],
    'L094': ['B0094'],
    'L095': ['B0095'],
    'L096': ['B0096'],
    'L097': ['B0097'],
    'L098': ['B0098'],
    'L099': ['B0099'],
    'L100': ['B0100'],
    'L101': ['B0101'],
    'L102': ['B0102'],
    'L103': ['B0103'],
    'L104': ['B0104'],
    'L105': ['B0105'],
    'L106': ['B0106'],
    'L107': ['B0107'],
    'L108': ['B0108'],
    'L109': ['B0109'],
    'L110': ['B0110'],
    'L111': ['B0111'],
    'L112': ['B0112'],
    'L113': ['B0113'],
    'L114': ['B0114'],
    'L115': ['B0115'],
    'L116': ['B0116'],
    'L117': ['B0117'],
    'L118': ['B0118'],
    'L119': ['B0119'],
    'L120': ['B0120'],
    'L121': ['B0121'],
    'L122': ['B0122'],
    'L123': ['B0123'],
    'L124': ['B0124'],
    'L125': ['B0125'],
    'L126': ['B0126'],
    'L127': ['B0127'],
    'L128': ['B0128'],
    'L129': ['B0129'],
    'L130': ['B0130'],
    'L131': ['B0131'],
    'L132': ['B0132'],
    'L133': ['B0133'],
    'L134': ['B0134'],
    'L135': ['B0135'],
    'L136': ['B0136'],
    'L137': ['B0137'],
    'L138': ['B0138'],
    'L139': ['B0139'],
    'L140': ['B0140'],
    'L141': ['B0141'],
    'L142': ['B0142'],
    'L143': ['B0143'],
    'L144': ['B0144'],
    'L145': ['B0145'],
    'L146': ['B0146'],
    'L147': ['B0147'],
    'L148': ['B0148'],
    'L149': ['B0149'],
    'L150': ['B0150'],
    'L151': ['B0151'],
    'L152': ['B0152'],
    'L153': ['B0153'],
    'L154': ['B0154'],
    'L155': ['B0155'],
    'L156': ['B0156'],
    'L157': ['B0157'],
    'L158': ['B0158'],
    'L159': ['B0159'],
    'L160': ['B0160'],
    'L161': ['B0161'],
    'L162': ['B0162'],
    'L163': ['B0163'],
    'L164': ['B0164'],
    'L165': ['B0165'],
    'L166': ['B0166'],
    'L167': ['B0167'],
    'L168': ['B0168'],
    'L169': ['B0169'],
    'L170': ['B0170'],
    'L171': ['B0171'],
    'L172': ['B0172'],
    'L173': ['B0173'],
    'L174': ['B0174'],
    'L175': ['B0175'],
    'L176': ['B0176'],
    'L177': ['B0177'],
    'L178': ['B0178'],
    'L179': ['B0179'],
    'L180': ['B0180'],
    'L181': ['B0181'],
    'L182': ['B0182'],
    'L183': ['B0183'],
    'L184': ['B0184'],
    'L185': ['B0185'],
    'L186': ['B0186'],
    'L187': ['B0187'],
    'L188': ['B0188'],
    'L189': ['B0189'],
    'L190': ['B0190'],
    'L191': ['B0191'],
    'L192': ['B0192'],
    'L193': ['B0193'],
    'L194': ['B0194'],
    'L195': ['B0195'],
    'L196': ['B0196'],
    'L197': ['B0197'],
    'L198': ['B0198'],
    'L199': ['B0199'],
    'L200': ['B0200'],
    'L201': ['B0201'],
    'L202': ['B0202'],
    'L203': ['B0203'],
    'L204': ['B0204'],
    'L205': ['B0205'],
    'L206': ['B0206'],
    'L207': ['B0207'],
    'L208': ['B0208'],
    'L209': ['B0209'],
    'L210': ['B0210'],
    'L211': ['B0211', 'B0212', 'B0213'],
    'L212': ['B0214', 'B0215', 'B0216'],
    'L213': ['B0217', 'B0218', 'B0219'],
    'L214': ['B0220', 'B0221', 'B0222'],
    'L215': ['B0223', 'B0224', 'B0225'],
    'L216': ['B0226', 'B0227'],
    'L217': ['B0228', 'B0229', 'B0230'],
    'L218': ['B0231', 'B0232', 'B0233'],
    'L219': ['B0234', 'B0235'],
    'L220': ['B0236', 'B0237', 'B0238'],
    'L221': ['B0239', 'B0240'],
    'L222': ['B0241', 'B0242'],
    'L223': ['B0243', 'B0244', 'B0245'],
    'L224': ['B0246', 'B0247', 'B0248'],
    'L225': ['B0249', 'B0250'],
    'L226': ['B0251'],
    'L227': ['B0251'],
    'L228': ['B0252'],
    'L229': ['B0252'],
    'L230': ['B0253'],
    'L231': ['B0253'],
    'L232': ['B0254'],
    'L233': ['B0254'],
    'L234': ['B0254'],
    'L235': ['B0255'],
    'L236': ['B0255'],
    'L237': ['B0256'],
    'L238': ['B0256'],
    'L239': ['B0257'],
    'L240': ['B0257'],
    'L241': ['B0258'],
    'L242': ['B0258'],
    'L243': ['B0259'],
    'L244': ['B0259'],
    'L245': ['B0260'],
    'L246': ['B0260'],
    'L247': ['B0261'],
    'L248': ['B0261'],
    'L249': ['B0261'],
    'L250': ['B0262'],
    'L251': ['B0262'],
    'L252': ['B0262'],
    'L253': ['B0263'],
    'L254': ['B0263'],
    'L255': ['B0263'],
    'L256': ['B0264'],
    'L257': ['B0264'],
    'L258': ['B0264'],
    'L259': ['B0265'],
    'L260': ['B0265'],
    'L261': ['B0266'],
    'L262': ['B0267'],
    'L263': ['B0268'],
    'L264': ['B0269'],
    'L265': ['B0270'],
    'L266': ['B0271'],
    'L267': ['B0272'],
    'L268': ['B0273'],
    'L269': ['B0274'],
    'L270': ['B0275'],
    'L271': ['B0276'],
    'L272': ['B0277'],
    'L273': ['B0278'],
    'L274': ['B0279'],
    'L275': ['B0280'],
    'L276': ['B0281'],
    'L277': ['B0282'],
    'L278': ['B0283'],
    'L279': ['B0284'],
    'L280': ['B0285'],
}

LEDGER_EXCEPTIONS = {'L281', 'L282', 'L283', 'L284', 'L285', 'L286', 'L287', 'L288', 'L289', 'L290', 'L291', 'L292', 'L293', 'L294', 'L295', 'L296', 'L297', 'L298', 'L299', 'L300'}

BANK_EXCEPTIONS = {'B0286', 'B0287', 'B0288', 'B0289', 'B0290', 'B0291', 'B0292', 'B0293', 'B0294', 'B0295', 'B0296', 'B0297', 'B0298', 'B0299', 'B0300', 'B0301', 'B0302', 'B0303', 'B0304', 'B0305'}

# (ledger_ids, bank_refs) pairs that are genuinely ambiguous — see docstring
AMBIGUOUS_NEAR_DUPLICATES = [
    (['L261', 'L262'], ['B0266', 'B0267']),
    (['L263', 'L264'], ['B0268', 'B0269']),
    (['L265', 'L266'], ['B0270', 'B0271']),
    (['L267', 'L268'], ['B0272', 'B0273']),
    (['L269', 'L270'], ['B0274', 'B0275']),
    (['L271', 'L272'], ['B0276', 'B0277']),
    (['L273', 'L274'], ['B0278', 'B0279']),
    (['L275', 'L276'], ['B0280', 'B0281']),
    (['L277', 'L278'], ['B0282', 'B0283']),
    (['L279', 'L280'], ['B0284', 'B0285']),
]


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

    # Build a lookup: ledger_id -> the full set of bank_refs valid for its ambiguous group
    ambiguous_lookup = {}
    for lids, brefs in AMBIGUOUS_NEAR_DUPLICATES:
        for lid in lids:
            ambiguous_lookup[lid] = set(brefs)

    for ledger_id, expected_refs in EXPECTED_MATCHES.items():
        expected_set = set(expected_refs)

        if ledger_id in actual_match_map:
            actual_set = actual_match_map[ledger_id]
            if actual_set == expected_set:
                exact_matches.append(ledger_id)
            elif ledger_id in ambiguous_lookup and actual_set.issubset(ambiguous_lookup[ledger_id]) and len(actual_set) == len(expected_set):
                exact_matches.append(ledger_id)  # valid alternate pairing within the ambiguous group
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