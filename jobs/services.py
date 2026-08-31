"""
ReconciliationEngine — replaces the old TransactionProcessor.

two CSVs in (bank, ledger) -> clean both -> MATCH rows across the two files
-> whatever doesn't match becomes an "exception" -> summarize match rate.
This is a cross-referencing / MATCHING problem, which is a different shape
of algorithm even though a lot of the cleaning code looks similar.

Pipeline, top to bottom:
    1. load_csv()          - read each file into a DataFrame
    2. clean_ledger/_bank() - normalize dates, amounts, currency, counterparty names
    3. match()              - the actual reconciliation:
         Phase 1: 1:1 matching (exact + fuzzy) between ledger rows and bank rows
         Phase 2: split/combine matching (1 ledger row <-> many bank rows, or vice
                  versa) for whatever Phase 1 couldn't resolve
         Phase 3: whatever is STILL unresolved after both phases becomes an
                  exception, on whichever side it's missing from
    4. build_summary()     - match rate math + LLM-written narrative

Tunable thresholds live as class constants at the top — these are the "policy"
decisions of your reconciliation logic (how close is "close enough"), so keep them
in one visible place rather than scattered as magic numbers through the code.
"""

import os
import re
import time
import json
import logging
import itertools
from difflib import SequenceMatcher
import pandas as pd
from dotenv import load_dotenv
from groq import Groq
from django.conf import settings

load_dotenv()
logger = logging.getLogger(__name__)

# ! OPTIMIZE ALL OF THIS
# ! INCREASE THROUGHPUT
# ! PRIORITY URGENT

class ReconciliationEngine:

    DATE_TOLERANCE_DAYS = 7

    NAME_SIMILARITY_THRESHOLD = 0.45
    # AMOUNT_TOLERANCE_ABS = 50.00
    AMOUNT_TOLERANCE_PERCENTAGE = 5.00

    # When looking for split/combined transactions, how many bank (or ledger) rows
    # are we willing to sum together to explain one row on the other side? Keep
    # ! this small — combinatorics blow up fast, and in practice most real splits are 2-4 way.
    # ! move this small further to 3 even 2 o(n^4) is a time bomb
    # ? shouldn't movig this to 3 mkae things faster i see no noticable difference in my stress test?
    MAX_COMBINATION_SIZE = 2

    # Common legal-entity suffixes that add noise to name matching without adding
    # information ("Acme Vendors" and "Acme Vendors Pvt Ltd" are the same vendor).
    NAME_SUFFIXES_TO_STRIP = ["PVT", "LTD", "LIMITED", "LLC", "INC", "PRIVATE", "CO", "COMPANY", "CORP", "CORPORATION",]

    def process(self, bank_file, ledger_file):
        # * loading both ledger_file and bank_file
        ledger_df = self.load_csv(ledger_file)
        bank_df = self.load_csv(bank_file)
        ledger_df = self.clean_ledger(ledger_df)
        bank_df = self.clean_bank(bank_df)

        matches, ledger_exceptions, bank_exceptions = self.match(ledger_df, bank_df)
        summary = self.build_summary(ledger_df, bank_df, matches, ledger_exceptions, bank_exceptions)

        return {
            "summary": summary,
            "matches": matches,
            "ledger_exceptions": ledger_exceptions,
            "bank_exceptions": bank_exceptions,
            "row_count_ledger": len(ledger_df),
            "row_count_bank": len(bank_df),
        }

    # ! time complexity - o(1)
    def load_csv(self, file):
        # * to set the cursor to the start of the file and start, without this there can be partial reads
        file.seek(0)
        return pd.read_csv(file)

    # ! time complexity - o(1)
    def clean_ledger(self, df):
        df = df.copy()
        df['date'] = pd.to_datetime(df['date'], format='mixed').dt.date
        df['amount'] = self._parse_amount(df['amount'])
        df['currency'] = df['currency'].str.upper().str.strip()
        df['normalized_name'] = df['counterparty'].apply(self._normalize_name)
        return df

    # ! time complexity - o(1)
    def clean_bank(self, df):
        df = df.copy()
        df['txn_date'] = pd.to_datetime(df['txn_date'], format='mixed').dt.date
        df['amount'] = self._parse_amount(df['amount'])
        df['currency'] = df['currency'].str.upper().str.strip()
        df['normalized_name'] = df['narration'].apply(self._normalize_name)
        return df

    # ! time complexity - o(1)
    def _parse_amount(self, amount_series):
        # * Strips anything that isn't a digit or a decimal point then casts to float. 
        return (amount_series.astype(str).str.replace(r'[^\d.\-]', '', regex=True).astype(float))

    # ! time complexity - o(no. of words)
    def _normalize_name(self, raw_name):
        """
        Turn a messy free-text name into something comparable:
        "  ACME VENDORS PVT LTD  " -> "ACME VENDORS"
        This is a heuristic, not a solved problem — genuinely different-looking
        names for the same entity 
        (see L015/B2015 in your test data: "Acme Vendors" vs "ACMEVEND TRF REF9284") will still slip past this.
        That's expected; those cases should fall to a human via the exception list,
        not get silently force-matched.
        """
        if pd.isna(raw_name):
            return ""
        name = str(raw_name).upper().strip()
        # ? why this we already have done strip() ? this makes no sense
        name = re.sub(r'\s+', ' ', name)  # collapse repeated whitespace
        # ? how does this work? no idea...
        tokens = [t for t in name.split(' ') if t not in self.NAME_SUFFIXES_TO_STRIP]
        return ' '.join(tokens)

    # ! time complexity - o(n)
    def _name_similarity(self, name_a, name_b):
        # ? what is a sequenceMatcher() ? is it an inbuilt func() doesn't seems like it?
        # ? and how does it even work?
        # ? what is the time complexity? is a fast approach possible
        return SequenceMatcher(None, name_a, name_b).ratio()

    def match(self, ledger_df, bank_df):
        matched_bank_idx = set()
        # ? set()? is used to have a set in python?
        matches = []
        unresolved_ledger_idx = []

        # ---- Phase 1: 1:1 matching ----
        # For each ledger row, look at bank rows within the date tolerance window
        # that haven't been claimed yet, score them, and take the best one if it
        # clears both the amount and name thresholds.
        # ! this is o(n^3)
        for l_idx, ledger_row in ledger_df.iterrows():
            # ! this func is o(n^2)
            best_bank_idx, best_score, best_meta = self._find_best_single_match(ledger_row, bank_df, matched_bank_idx)

            # * if there is no error...
            # * put a reacordd of ledger row and bank row in matches array...
            if best_bank_idx is not None:
                # * we are getting bank row to store it in matches... ASSUMPTION check it later
                bank_row = bank_df.loc[best_bank_idx]
                matches.append(self._build_match_record(
                    ledger_row, [bank_row], best_meta
                ))
                matched_bank_idx.add(best_bank_idx)
            # * if there is an error do this put it in unresolved_ledger_idx array
            else:
                unresolved_ledger_idx.append(l_idx)

        # ---- Phase 2a: split matching (1 ledger row -> many bank rows) ----
        still_unresolved_ledger = []
        # ! o((n^2r)
        for l_idx in unresolved_ledger_idx:
            ledger_row = ledger_df.loc[l_idx]
            # ! o(n^matchconstant)
            combo, meta = self._find_combination_match(
                target_row=ledger_row,
                target_amount=ledger_row['amount'],
                candidate_df=bank_df,
                excluded_idx=matched_bank_idx,
                candidate_name_col='normalized_name',
                candidate_date_col='txn_date',
                target_date=ledger_row['date'],
            )
            if combo:
                bank_rows = [bank_df.loc[i] for i in combo]
                matches.append(self._build_match_record(ledger_row, bank_rows, meta))
                matched_bank_idx.update(combo)
            else:
                still_unresolved_ledger.append(l_idx)

        # ---- Phase 2b: combine matching (many ledger rows -> 1 bank row) ----
        # Group whatever's still unresolved by normalized name, and check whether
        # a *group* of ledger rows sums to a single remaining bank row.
        # ? can this be optimized?
        # * this can be done by vector operations
        # remaining_bank_idx = [i for i in bank_df.index if i not in matched_bank_idx]
        remaining_bank_idx = bank_df.index[~bank_df.index.isin(matched_bank_idx)].tolist()
        remaining_bank_set = set(remaining_bank_idx)
        resolved_ledger_via_combine = set()
        bank_by_name = bank_df.groupby("normalized_name").groups

        # so we are normalizaing the rows by similar name, seems fine
        # doesn't seem a way to optimize it further
        for name, group in ledger_df.loc[still_unresolved_ledger].groupby('normalized_name'):
            if not name:
                continue
            candidate_idx_pool = [i for i in bank_by_name.get(name, []) if i in remaining_bank_set]

            if not candidate_idx_pool:
                continue

            # Extract these once.
            # We don't want to repeatedly access ledger_df with .loc
            # inside the combinations loop.
            group_indices = group.index.tolist()
            group_amounts = group["amount"].to_dict()
            group_dates = group["date"].to_dict()
            for combo_size in range(2, self.MAX_COMBINATION_SIZE + 1):
                if len(resolved_ledger_via_combine) >= len(group_indices):
                    break
                for ledger_combo in itertools.combinations(group_indices, combo_size):
                    if any(i in resolved_ledger_via_combine for i in ledger_combo):
                        continue

                    combo_amount = sum(group_amounts[i] for i in ledger_combo)
                    combo_date = max(group_dates[i] for i in ledger_combo)

                    # combo_amount = ledger_df.loc[list(ledger_combo), 'amount'].sum()
                    # combo_date = ledger_df.loc[list(ledger_combo), 'date'].max()

                    # ! time complexity o(n*word)
                    match_bank_idx = self._find_single_amount_match(
                        target_amount=combo_amount,
                        target_date=combo_date,
                        target_name=name,
                        candidate_df=bank_df,
                        candidate_idx_pool=remaining_bank_idx,
                    )

                    if match_bank_idx is None:
                        continue

                    ledger_rows = [ledger_df.loc[i] for i in ledger_combo]
                    bank_row = bank_df.loc[match_bank_idx]
                    matches.append(self._build_combine_match_record(ledger_rows, bank_row))
                    matched_bank_idx.add(match_bank_idx)
                    remaining_bank_idx.remove(match_bank_idx)
                    resolved_ledger_via_combine.update(ledger_combo)

                    # Remove the matched bank row from this group's
                    # candidate pool so we don't search it again.
                    candidate_idx_pool.remove(match_bank_idx)

                    if not candidate_idx_pool:
                        break
                    
                if not candidate_idx_pool:
                    break

        final_unresolved_ledger = [i for i in still_unresolved_ledger if i not in resolved_ledger_via_combine]

        # ---- Phase 3: whatever's left is a genuine exception ----
        ledger_exceptions = [self._build_ledger_exception(ledger_df.loc[i]) for i in final_unresolved_ledger]
        bank_exceptions = [
            self._build_bank_exception(bank_df.loc[i])
            for i in bank_df.index if i not in matched_bank_idx
        ]

        return matches, ledger_exceptions, bank_exceptions

    # -- Phase 1 helper: score every viable bank candidate for one ledger row ----
    # ! I THINK THIS IS DONE FOR NOW
    def _find_best_single_match(self, ledger_row, bank_df, excluded_idx):
        available = bank_df[~bank_df.index.isin(excluded_idx)]
        if available.empty:
            return None, -1, None

        # Vectorized date diff — works if txn_date is datetime64; falls back safely if it's plain `date` objects
        date_diff = (available['txn_date'] - ledger_row['date']).abs()
        if date_diff.dtype == 'object':  # still python timedelta objects, not vectorized dtype
            date_diff = date_diff.apply(lambda td: td.days)
        else:
            date_diff = date_diff.dt.days

        amount_diff = (available['amount'] - ledger_row['amount']).abs()
        amount_maxi = pd.concat([available['amount'], pd.Series(ledger_row['amount'], index=available.index)], axis=1).max(axis=1)
        amount_pct = amount_diff / amount_maxi * 100

        candidates_mask = (date_diff <= self.DATE_TOLERANCE_DAYS) & (amount_pct <= self.AMOUNT_TOLERANCE_PERCENTAGE)
        candidates = available[candidates_mask]
        if candidates.empty:
            return None, -1, None

        best_idx, best_score, best_meta = None, -1, None

        for b_idx, bank_row in candidates.iterrows():
            name_sim = self._name_similarity(ledger_row['normalized_name'], bank_row['normalized_name'])
            if name_sim < self.NAME_SIMILARITY_THRESHOLD:
                continue

            # Currency is checked but NOT used to exclude a candidate — a currency
            # mismatch (see L006/B2006 in your test set) is exactly the kind of
            # thing reconciliation is supposed to catch, not hide by filtering it
            # out before you ever see it.
            # ? how can amount be ok if there is a currency_mismatch
            # ? this logic seems broken because of exchange rates
            currency_mismatch = ledger_row['currency'] != bank_row['currency']
            dd = date_diff[b_idx]
            ad = amount_diff[b_idx]
            score = (
                (1 - dd / max(self.DATE_TOLERANCE_DAYS, 1)) * 0.3
                + (1 - min(ad / max(ledger_row['amount'], 1), 1)) * 0.4
                + name_sim * 0.3
                - (0.5 if currency_mismatch else 0)
            )
            if score > best_score:
                best_score, best_idx = score, b_idx
                best_meta = {"date_diff_days": dd, "amount_diff": round(ad, 2), "name_similarity": round(name_sim, 2), "currency_mismatch": currency_mismatch}

        return best_idx, best_score, best_meta

    # * LGTM
    # ! Candidate filtering: O(n * S), where S is name-similarity cost.
    # ! Combination search: O(sum(C(n,r) * r)) for r=2..MAX_COMBINATION_SIZE.
    # If MAX_COMBINATION_SIZE = k is constant, approximately O(n^k).
    # Practical performance depends heavily on the filtered pool size.
    # -- Phase 2a helper: try summing N candidates to hit a target amount -------
    def _find_combination_match(self, target_row, target_amount, candidate_df, excluded_idx, candidate_name_col, candidate_date_col, target_date,):
        available = candidate_df[~candidate_df.index.isin(excluded_idx)]
        date_diff = (available['txn_date'] - target_date).abs()
        if date_diff.dtype == 'object':
            date_diff = date_diff.apply(lambda td: td.days)
        else:
            date_diff = date_diff.dt.days

        date_mask = date_diff <= self.DATE_TOLERANCE_DAYS
        date_filtered = available[date_mask]

        name_sims = date_filtered[candidate_name_col].apply(lambda name: self._name_similarity(name, target_row['normalized_name']))
        pool = date_filtered.index[name_sims >= self.NAME_SIMILARITY_THRESHOLD].tolist()

        for combo_size in range(2, self.MAX_COMBINATION_SIZE + 1):
            for combo in itertools.combinations(pool, combo_size):
                combo_sum = candidate_df.loc[list(combo), 'amount'].sum()
                amount_diff = abs(combo_sum - target_amount)
                amount_maxi = max(combo_sum, target_amount)
                amount_percentage = amount_diff/amount_maxi*100;

                if amount_percentage <= self.AMOUNT_TOLERANCE_PERCENTAGE:
                    return list(combo), {"match_type": "split", "bank_row_count": combo_size, "combined_amount": round(combo_sum, 2),}
        return None, None

    # * LGTM DONE
    # -- Phase 2b helper: find one bank row matching a combined ledger amount ---
    # ! time complexity o(n*word)
    def _find_single_amount_match(self, target_amount, target_date, target_name, candidate_df, candidate_idx_pool):
        # *** som something like this can work***
        candidates = candidate_df.loc[candidate_idx_pool]
        if candidates.empty:
            return None

        date_diff = (candidates['txn_date'] - target_date).abs()
        if date_diff.dtype == 'object':
            date_diff = date_diff.apply(lambda td: td.days)
        else:
            date_diff = date_diff.dt.days

        amount_diff = (candidates['amount'] - target_amount).abs()
        amount_maxi = pd.concat([candidates['amount'], pd.Series(target_amount, index=candidates.index)], axis=1).max(axis=1)
        amount_pct = amount_diff / amount_maxi * 100

        candidates_mask = (date_diff <= self.DATE_TOLERANCE_DAYS) & (amount_pct <= self.AMOUNT_TOLERANCE_PERCENTAGE)
        candidates = candidates[candidates_mask]
        if candidates.empty:
            return None

        for b_idx, bank_row in candidates.iterrows():

            name_sim = self._name_similarity(
                bank_row["normalized_name"],
                target_name,
            )

            if name_sim >= self.NAME_SIMILARITY_THRESHOLD:
                return b_idx

        return None

    # ! time complexity - o(1)
    def _build_match_record(self, ledger_row, bank_rows, meta):
        discrepancies = []
        if meta.get("date_diff_days", 0) > 0:
            discrepancies.append(f"date offset by {meta['date_diff_days']} day(s)")
        if meta.get("amount_diff", 0) > 0:
            discrepancies.append(f"amount differs by {meta['amount_diff']}")
        if meta.get("currency_mismatch"):
            discrepancies.append("currency mismatch between ledger and bank record")
        if meta.get("match_type") == "split":
            discrepancies.append(f"matched against {meta['bank_row_count']} combined bank records")

        confidence = "high" if not discrepancies else ("medium" if len(discrepancies) == 1 else "low")

        return {
            "ledger_id": ledger_row.get("transaction_id"),
            "bank_refs": [b.get("bank_ref") for b in bank_rows],
            "match_confidence": confidence,
            "discrepancies": discrepancies,
        }

    # ! time complexity - o(1)
    def _build_combine_match_record(self, ledger_rows, bank_row):
        ledger_ids = [r.get("transaction_id") for r in ledger_rows]
        return {
            "ledger_id": ledger_ids,  # list, since it's many-to-one
            "bank_refs": [bank_row.get("bank_ref")],
            "match_confidence": "medium",
            "discrepancies": [f"{len(ledger_ids)} ledger records combined to match one bank record"],
        }

    # ! time complexity - o(1)
    def _build_ledger_exception(self, ledger_row):
        return {
            "ledger_id": ledger_row.get("transaction_id"),
            "amount": ledger_row.get("amount"),
            "date": str(ledger_row.get("date")),
            "counterparty": ledger_row.get("counterparty"),
            "reason": "no bank record found within date/amount/name tolerance",
        }

    def _build_bank_exception(self, bank_row):
        narration = str(bank_row.get("narration", "")).upper()
        # Cheap heuristic reason-guessing based on common narration keywords —
        # this is a first pass; swap in the LLM narrative step below if you want
        # something smarter than keyword matching.
        if "FEE" in narration or "CHARGE" in narration:
            reason = "likely a bank fee/service charge never recorded in the ledger"
        elif "INTEREST" in narration:
            reason = "likely bank interest credit never recorded in the ledger"
        elif "REFUND" in narration or bank_row.get("amount", 0) < 0:
            reason = "likely a refund/reversal with no corresponding ledger entry"
        else:
            reason = "no ledger record found within date/amount/name tolerance — needs investigation"

        return {
            "bank_ref": bank_row.get("bank_ref"),
            "amount": bank_row.get("amount"),
            "date": str(bank_row.get("txn_date")),
            "narration": bank_row.get("narration"),
            "reason": reason,
        }

    # ! time complexity depends on AI CONNECTION
    def build_summary(self, ledger_df, bank_df, matches, ledger_exceptions, bank_exceptions):
        total_ledger = len(ledger_df)
        resolved_ledger = total_ledger - len(ledger_exceptions)
        match_rate = round((resolved_ledger / total_ledger) * 100, 1) if total_ledger else 0.0

        summary = {
            "total_ledger_records": total_ledger,
            "total_bank_records": len(bank_df),
            "resolved_ledger_records": resolved_ledger,
            "match_rate_percent": match_rate,
            "ledger_exception_count": len(ledger_exceptions),
            "bank_exception_count": len(bank_exceptions),
        }

        narrative = self._llm_narrative(summary)
        return {**summary, **narrative}

    # ! time complexity depends on AI CONNECTION
    def _llm_narrative(self, summary):
        prompt = f"""
        Given this reconciliation summary:

        {json.dumps(summary, indent=2)}

        Generate ONLY valid JSON:

        {{
            "narrative": "2-3 sentence plain-English summary of how clean this reconciliation run was",
            "risk_level": "low"
        }}

        risk_level must be one of: low, medium, high
        (base it on match_rate_percent and exception counts)

        Return only JSON. Do not use markdown. Do not use code fences.
        """
        try:
            content = self._call_llm_with_retry(prompt, "summary")
            return json.loads(content)
        except Exception as e:
            logger.warning(f"LLM narrative generation failed: {e}")
            return {"narrative": "Narrative unavailable.", "risk_level": "NA"}

    # * LGTM
    # ! time complexity depends on AI CONNECTION
    def _call_llm_with_retry(self, prompt, task):
        if settings.TESTING:
            return json.dumps({"narrative": "Mock summary", "risk_level": "low"})

        client = Groq(api_key=os.getenv("GROQ_API_KEY"))

        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model="openai/gpt-oss-20b",
                    temperature=0,
                )
                return response.choices[0].message.content
            except Exception as e:
                logger.warning(f"LLM attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    time.sleep(2 ** attempt)  # 1s, 2s, 4s

        raise Exception("All LLM retries failed")