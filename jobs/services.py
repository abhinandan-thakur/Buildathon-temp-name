"""
ReconciliationEngine

The engine is rewritten to eliminate the greedy bank-row commitment bug. The
matching process is now explicit:

1. Build candidate bank rows for each ledger row.
2. Rank candidates by a stable score.
3. Resolve assignments globally by strongest score.
4. Run split/combine logic only on unresolved rows.
5. Mark the leftovers as exceptions.

This keeps the public contract and output shape intact while fixing the actual
root cause behind many false pairings and lost matches.
"""

import itertools
import json
import logging
import os
import re
import time
from difflib import SequenceMatcher
from functools import cache

import pandas as pd
from django.conf import settings
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
logger = logging.getLogger(__name__)


class ReconciliationEngine:
    DATE_TOLERANCE_DAYS = 7
    EXTENDED_DATE_TOLERANCE_DAYS = 25
    NAME_SIMILARITY_THRESHOLD = 0.55
    AMOUNT_TOLERANCE_PERCENTAGE = 5.0
    MIN_MATCH_SCORE = 0.62
    MAX_COMBINATION_SIZE = 3
    NAME_SUFFIXES_TO_STRIP = ["PVT","LTD","LIMITED","LLC","INC","PRIVATE","CO","COMPANY","CORP","CORPORATION",]

    def process(self, bank_file, ledger_file):
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

    def load_csv(self, file):
        file.seek(0)
        return pd.read_csv(file)

    def clean_ledger(self, df):
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"], format="mixed").dt.date
        df["amount"] = self._parse_amount(df["amount"])
        df["currency"] = df["currency"].fillna("").astype(str).str.upper().str.strip()
        df["normalized_name"] = df["counterparty"].apply(self._normalize_name)
        return df

    def clean_bank(self, df):
        df = df.copy()
        df["txn_date"] = pd.to_datetime(df["txn_date"], format="mixed").dt.date
        df["amount"] = self._parse_amount(df["amount"])
        df["currency"] = df["currency"].fillna("").astype(str).str.upper().str.strip()
        df["normalized_name"] = df["narration"].apply(self._normalize_name)
        return df

    def _parse_amount(self, amount_series):
        cleaned = amount_series.astype(str).str.replace(r"[^\d\.\-]", "", regex=True)
        cleaned = cleaned.replace("", pd.NA)
        return pd.to_numeric(cleaned, errors="coerce").fillna(0.0)

    def _normalize_name(self, raw_name):
        if pd.isna(raw_name):
            return ""
        name = str(raw_name).upper().strip()
        name = re.sub(r"\s+", " ", name)
        tokens = [token for token in name.split(" ") if token not in self.NAME_SUFFIXES_TO_STRIP]
        return " ".join(tokens)

    @staticmethod
    @cache
    def _name_similarity_cached(name_a, name_b):
        if not name_a or not name_b:
            return 0.0
        ratio = SequenceMatcher(None, name_a, name_b).ratio()
        # ? wtf? is this? why?
        shorter, longer = (name_a, name_b) if len(name_a) <= len(name_b) else (name_b, name_a)
        if shorter and (longer == shorter or longer.startswith(shorter + " ")):
            ratio = max(ratio, 0.75)
        return ratio
    
    def _name_similarity(self, name_a, name_b):
        if(name_a <= name_b):
            return self._name_similarity_cached(name_a, name_b)
        return self._name_similarity_cached(name_b, name_a)

    # ? How many functions are calling this? tbh?
    def _effective_date_tolerance(self, ledger_row, bank_row):
        amount_delta = abs(float(bank_row["amount"]) - float(ledger_row["amount"]))
        if amount_delta > 2.0:
            return self.DATE_TOLERANCE_DAYS
        name_sim = self._name_similarity(ledger_row["normalized_name"], bank_row["normalized_name"])
        if name_sim >= 0.9 and amount_delta <= 1.0:
            return self.EXTENDED_DATE_TOLERANCE_DAYS
        if name_sim >= 0.8 and amount_delta <= 2.0:
            return self.EXTENDED_DATE_TOLERANCE_DAYS
        return self.DATE_TOLERANCE_DAYS

    def match(self, ledger_df, bank_df):
        ledger_used = set()
        bank_used = set()
        matches = []

        # Phase 1: strong single-row matches are assigned by highest score, with each
        # bank row being claimed at most once. This avoids the previous bug where one
        # weak local suggestion consumed a bank ow before a stronger global choice.
        candidate_pairs = []
        # ! is this time complexity o(n^3*name)
        for l_idx, ledger_row in ledger_df.iterrows():
            for bank_idx, score, meta in self._candidate_bank_rows_for_ledger(ledger_row, bank_df, bank_used):
                candidate_pairs.append((score, l_idx, bank_idx, meta))

        candidate_pairs.sort(key=lambda x: x[0], reverse=True)

        for score, l_idx, bank_idx, meta in candidate_pairs:
            if score < self.MIN_MATCH_SCORE:
                continue
            if l_idx in ledger_used or bank_idx in bank_used:
                continue

            ledger_row = ledger_df.loc[l_idx]
            bank_row = bank_df.loc[bank_idx]
            matches.append(self._build_match_record(ledger_row, [bank_row], meta))
            ledger_used.add(l_idx)
            bank_used.add(bank_idx)

        unresolved_ledger = [idx for idx in ledger_df.index if idx not in ledger_used]

        # Rescue pass for exact-vendor, exact-amount rows that are valid but were
        # excluded by the tighter 7-day window. This keeps the filter conservative,
        # but avoids losing rows that are otherwise unique, exact matches.
        rescued = set()
        for l_idx in list(unresolved_ledger):
            ledger_row = ledger_df.loc[l_idx]
            bank_idx = self._find_exact_rescue_match(ledger_row, bank_df, bank_used)
            if bank_idx is None:
                continue
            bank_row = bank_df.loc[bank_idx]
            matches.append(self._build_match_record(ledger_row, [bank_row], {
                "date_diff_days": int(abs((bank_row["txn_date"] - ledger_row["date"]).days)),
                "amount_diff": round(float(abs(bank_row["amount"] - ledger_row["amount"])), 2),
                "name_similarity": round(float(self._name_similarity(ledger_row["normalized_name"], bank_row["normalized_name"])), 2),
                "currency_mismatch": ledger_row["currency"] != bank_row["currency"],
                "match_type": "rescue",
            }))
            ledger_used.add(l_idx)
            bank_used.add(bank_idx)
            rescued.add(l_idx)

        unresolved_ledger = [idx for idx in unresolved_ledger if idx not in rescued]

        # Phase 2a: 1 ledger -> many bank rows (split transactions)
        still_unresolved = []
        for l_idx in unresolved_ledger:
            combo, meta = self._find_combination_match(
                target_row=ledger_df.loc[l_idx],
                target_amount=ledger_df.loc[l_idx, "amount"],
                candidate_df=bank_df,
                excluded_idx=bank_used,
                candidate_name_col="normalized_name",
                candidate_date_col="txn_date",
                target_date=ledger_df.loc[l_idx, "date"],
            )
            if combo:
                bank_rows = [bank_df.loc[i] for i in combo]
                matches.append(self._build_match_record(ledger_df.loc[l_idx], bank_rows, meta))
                bank_used.update(combo)
                ledger_used.add(l_idx)
            else:
                still_unresolved.append(l_idx)

        # Phase 2b: many ledger rows -> 1 bank row (combined transactions)
        remaining_bank = [idx for idx in bank_df.index if idx not in bank_used]
        resolved_grouped = set()

        for name, group in ledger_df.loc[still_unresolved].groupby("normalized_name"):
            if not name:
                continue

            group_indices = group.index.tolist()
            group_amounts = group["amount"].to_dict()
            group_dates = group["date"].to_dict()

            for combo_size in range(2, self.MAX_COMBINATION_SIZE + 1):
                for ledger_combo in itertools.combinations(group_indices, combo_size):
                    if any(i in resolved_grouped for i in ledger_combo):
                        continue

                    combo_amount = sum(group_amounts[i] for i in ledger_combo)
                    combo_date = max(group_dates[i] for i in ledger_combo)
                    bank_idx = self._find_single_amount_match(
                        target_amount=combo_amount,
                        target_date=combo_date,
                        target_name=name,
                        candidate_df=bank_df,
                        candidate_idx_pool=remaining_bank,
                    )
                    if bank_idx is None:
                        continue

                    ledger_rows = [ledger_df.loc[i] for i in ledger_combo]
                    matches.append(self._build_combine_match_record(ledger_rows, bank_df.loc[bank_idx]))
                    bank_used.add(bank_idx)
                    remaining_bank.remove(bank_idx)
                    resolved_grouped.update(ledger_combo)
                    ledger_used.update(ledger_combo)
                    if not remaining_bank:
                        break
                if not remaining_bank:
                    break

        final_unresolved_ledger = [idx for idx in still_unresolved if idx not in resolved_grouped]

        ledger_exceptions = [self._build_ledger_exception(ledger_df.loc[idx]) for idx in final_unresolved_ledger]
        bank_exceptions = [
            self._build_bank_exception(bank_df.loc[idx])
            for idx in bank_df.index if idx not in bank_used
        ]

        return matches, ledger_exceptions, bank_exceptions

    # ! time compmlexity o(n*name)
    def _candidate_bank_rows_for_ledger(self, ledger_row, bank_df, bank_used):
        available = bank_df if not bank_used else bank_df[~bank_df.index.isin(bank_used)]
        if available.empty:
            return []

        date_diff = (available["txn_date"] - ledger_row["date"]).abs()
        if hasattr(date_diff, "dt"):
            date_diff = date_diff.dt.days
        else:
            date_diff = date_diff.apply(lambda td: td.days)

        candidate_rows = []
        name_scores = {}
        for bank_row in available.itertuples(index=True):
            b_idx = bank_row.Index
            name_sim = self._name_similarity(ledger_row["normalized_name"], bank_row.normalized_name)
            if name_sim < self.NAME_SIMILARITY_THRESHOLD:
                continue
            amount_delta = abs(float(bank_row.amount) - float(ledger_row["amount"]))
            if amount_delta > 2.0:
                date_tol = self.DATE_TOLERANCE_DAYS
            elif name_sim >= 0.9 and amount_delta <= 1.0:
                date_tol = self.EXTENDED_DATE_TOLERANCE_DAYS
            elif name_sim >= 0.8 and amount_delta <= 2.0:
                date_tol = self.EXTENDED_DATE_TOLERANCE_DAYS
            else:
                date_tol = self.DATE_TOLERANCE_DAYS
            if int(date_diff[b_idx]) > date_tol:
                continue
            candidate_rows.append(b_idx)
            name_scores[b_idx] = name_sim

        if not candidate_rows:
            return []

        available = available.loc[candidate_rows]
        date_diff = date_diff.loc[candidate_rows]

        amount_diff = (available["amount"] - ledger_row["amount"]).abs()
        denom = available["amount"].replace(0, pd.NA).fillna(abs(ledger_row["amount"]))
        amount_pct = (amount_diff / denom) * 100
        candidates = available[amount_pct <= self.AMOUNT_TOLERANCE_PERCENTAGE]
        if candidates.empty:
            return []

        scored = []
        for bank_row in candidates.itertuples(index=True):
            b_idx = bank_row.Index
            name_sim = name_scores[b_idx]
            date_delta_days = int(date_diff[b_idx])
            amount_delta = float(amount_diff[b_idx])
            dynamic_date_tolerance = self.DATE_TOLERANCE_DAYS
            if amount_delta <= 2.0 and (
                (name_sim >= 0.9 and amount_delta <= 1.0)
                or (name_sim >= 0.8 and amount_delta <= 2.0)
            ):
                dynamic_date_tolerance = self.EXTENDED_DATE_TOLERANCE_DAYS
            date_score = max(0.0, 1.0 - (date_delta_days / max(dynamic_date_tolerance, 1)))
            amount_score = max(0.0, 1.0 - (amount_delta / max(abs(ledger_row["amount"]), 1)))
            currency_mismatch = ledger_row["currency"] != bank_row.currency
            currency_penalty = 0.5 if currency_mismatch else 0.0
            score = 0.45 * name_sim + 0.35 * amount_score + 0.20 * date_score - currency_penalty
            meta = {
                "date_diff_days": date_delta_days,
                "amount_diff": round(amount_delta, 2),
                "name_similarity": round(float(name_sim), 2),
                "currency_mismatch": currency_mismatch,
            }
            scored.append((b_idx, score, meta))

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:8]

    def _score_single_candidate(self, ledger_row, bank_row, date_delta_days, amount_delta, name_sim):
        dynamic_date_tolerance = self._effective_date_tolerance(ledger_row, bank_row)
        date_score = max(0.0, 1.0 - (date_delta_days / max(dynamic_date_tolerance, 1)))
        amount_score = max(0.0, 1.0 - (amount_delta / max(abs(ledger_row["amount"]), 1)))
        currency_penalty = 0.5 if ledger_row["currency"] != bank_row["currency"] else 0.0

        score = (0.45 * name_sim+ 0.35 * amount_score+ 0.20 * date_score- currency_penalty)
        return score

    def _find_exact_rescue_match(self, ledger_row, bank_df, bank_used):
        # ? so avail has all but not which are not in bank used good but why.copt()
        # ? isn't copy an overhead operation wouldn't straight up passing reference eb bettwer an dfaster
        available = bank_df[~bank_df.index.isin(bank_used)]
        if available.empty:
            return None

        candidates = []
        for b_idx, bank_row in available.iterrows():
            # * moving name similarity below because it has hig computational cost
            # * hopin this won't break anything let me just check before and after result
            # * BEFORE 
            # * Engine-reported match rate: 89.3% Match recall:                 92.1% (258/280) Wrong-match rate:             2.1%
            # * Ledger exception accuracy:    80.0% Bank exception accuracy:      100.0% Overall ledger accuracy:      91.3%
            # * Exact matches:   258 Partial matches: 6 Missed matches:  16
            # * AFTER
            # * Engine-reported match rate: 89.3% Match recall:                 92.1% (258/280) Wrong-match rate:             2.1%
            # * Ledger exception accuracy:    80.0% Bank exception accuracy:      100.0% Overall ledger accuracy:      91.3%
            # * Exact matches:   258 Partial matches: 6 Missed matches:  16

            amount_delta = abs(float(bank_row["amount"]) - float(ledger_row["amount"]))
            if amount_delta > max(2.0, abs(float(ledger_row["amount"])) * 0.02):
                continue
            date_delta = abs((bank_row["txn_date"] - ledger_row["date"]).days)
            if date_delta > 30:
                continue
            name_sim = self._name_similarity(ledger_row["normalized_name"], bank_row["normalized_name"])
            if name_sim < 0.58:
                continue
            score = (
                0.6 * name_sim
                + 0.35 * max(0.0, 1.0 - (amount_delta / max(abs(float(ledger_row["amount"])), 1)))
                + 0.05 * max(0.0, 1.0 - (date_delta / 30.0))
            )
            candidates.append((score, b_idx))

        if not candidates:
            return None
        
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_idx = candidates[0]
        if len(candidates) > 1 and candidates[1][0] > best_score * 0.995:
            return None
        return best_idx

    def _find_combination_match(self,target_row,target_amount,candidate_df,excluded_idx,candidate_name_col,candidate_date_col,target_date,):
        # ? again? copy()
        available = candidate_df if not excluded_idx else candidate_df[~candidate_df.index.isin(excluded_idx)]
        if available.empty:
            return None, None

        date_diff = (available[candidate_date_col] - target_date).abs()
        if hasattr(date_diff, "dt"):
            date_diff = date_diff.dt.days
        else:
            date_diff = date_diff.apply(lambda td: td.days)

        filtered = []
        name_scores = {}
        # ? yup, we should filter the dataset first on data diff? that is understandable?
        # ? But my question why not do target amount here only too? another filter and it would be way better off?
        for idx, row in available.iterrows():
            # ? i think there i still a way to kind of avoid name_similarity() ? 
            # ? here is what i think?
            # ? check if the amount_delta is greater > 2 and data diff > DATE_TOLERANCE_DAYS: continue?
            # ? maybe i am wrong?
            name_sim = self._name_similarity(target_row["normalized_name"], row[candidate_name_col])
            if name_sim < self.NAME_SIMILARITY_THRESHOLD:
                continue
            amount_delta = abs(float(row["amount"]) - float(target_amount))
            effective_tol = self.EXTENDED_DATE_TOLERANCE_DAYS if name_sim >= 0.8 and amount_delta <= 2.0 else self.DATE_TOLERANCE_DAYS
            if int(date_diff[idx]) <= effective_tol:
                filtered.append(idx)
                name_scores[idx] = name_sim
        if not filtered:
            return None, None

        # ? wait we are iterating again? to store name_similarity_scores?
        # ? i think this is redunandant and can be done in the previous for loop?
        # ? are we finding the best row for the target row using a for loop ? than maybe i am wrong?
        pool = filtered
        if len(pool) < 2:
            return None, None

        best_combo = None
        best_score = -float("inf")
        best_meta = None
        amount_by_idx = available["amount"].to_dict()

        for combo_size in range(2, min(len(pool), self.MAX_COMBINATION_SIZE) + 1):
            for combo in itertools.combinations(pool, combo_size):
                combo_amount = sum(amount_by_idx[i] for i in combo)
                amount_pct = abs(combo_amount - target_amount) / max(abs(target_amount), 1) * 100
                if amount_pct > self.AMOUNT_TOLERANCE_PERCENTAGE:
                    continue

                name_score = sum(name_scores[i] for i in combo) / combo_size
                date_score = sum(
                    max(0.0, 1.0 - (abs((available.loc[i, candidate_date_col] - target_date).days) / self.DATE_TOLERANCE_DAYS))
                    for i in combo
                ) / combo_size
                score = (0.55 * name_score) + (0.30 * min(1.0, 1.0 - amount_pct / 100.0)) + (0.15 * date_score)

                if score > best_score:
                    best_score = score
                    best_combo = list(combo)
                    best_meta = {
                        "match_type": "split",
                        "bank_row_count": combo_size,
                        "combined_amount": round(combo_amount, 2),
                        "score": round(score, 3),
                    }

        if best_combo is None:
            return None, None
        return best_combo, best_meta

    def _find_single_amount_match(self, target_amount, target_date, target_name, candidate_df, candidate_idx_pool):
        if not candidate_idx_pool:
            return None

        # ? copy() isn't just storing reference would be faster?
        # ? does python even work like that? i mean in ref?
        candidates = candidate_df.loc[candidate_idx_pool]
        if candidates.empty:
            return None

        date_diff = (candidates["txn_date"] - target_date).abs()
        if hasattr(date_diff, "dt"):
            date_diff = date_diff.dt.days
        else:
            date_diff = date_diff.apply(lambda td: td.days)

        amount_diff = (candidates["amount"] - target_amount).abs()
        denom = candidates["amount"].replace(0, pd.NA).fillna(abs(target_amount))
        amount_pct = (amount_diff / denom) * 100

        best_idx = None
        best_score = -float("inf")

        # ! Confused? wtf is this?
        for b_idx, bank_row in candidates.iterrows():
            name_sim = self._name_similarity(bank_row["normalized_name"], target_name)
            if name_sim < self.NAME_SIMILARITY_THRESHOLD:
                continue
            # ! what is this? so unreadable
            effective_tol = self.EXTENDED_DATE_TOLERANCE_DAYS if name_sim >= 0.8 and amount_diff[b_idx] <= 2.0 else self.DATE_TOLERANCE_DAYS
            if int(date_diff[b_idx]) <= effective_tol and amount_pct[b_idx] <= self.AMOUNT_TOLERANCE_PERCENTAGE:
                date_score = max(0.0, 1.0 - (date_diff[b_idx] / self.DATE_TOLERANCE_DAYS))
                amount_score = max(0.0, 1.0 - (amount_diff[b_idx] / max(abs(target_amount), 1)))
                score = 0.4 * name_sim + 0.35 * amount_score + 0.25 * date_score
                if score > best_score:
                    best_score = score
                    best_idx = b_idx

        return best_idx

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

    def _build_combine_match_record(self, ledger_rows, bank_row):
        ledger_ids = [r.get("transaction_id") for r in ledger_rows]
        return {
            "ledger_id": ledger_ids,
            "bank_refs": [bank_row.get("bank_ref")],
            "match_confidence": "medium",
            "discrepancies": [f"{len(ledger_ids)} ledger records combined to match one bank record"],
        }

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
                    time.sleep(2 ** attempt)
        raise Exception("All LLM retries failed")
