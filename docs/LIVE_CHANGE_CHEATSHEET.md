# Live change cheat sheet (for the 30-minute call)

All company thresholds are constants at the top of `src/rule_engine.py` (lines ~35-62). Change a number, run the tests, show the effect.

| Request | Where | What happens |
|---|---|---|
| Price tolerance 3% -> 5% | `PRICE_REJECT_EXCESS_RATIO = Decimal("0.05")` | pack 03 (9% over) still rejects; pack 04 (2.3%) unchanged |
| Advance limit 20% -> 10% | `ADVANCE_REJECT_THRESHOLD_PCT` | pack 03 (15% advance) now also fails R-02; the 10% packs are unchanged (exactly 10 is not more than 10); pack 05 still rejects |
| Max delivery 45 -> 30 days | `DELIVERY_REJECT_DAYS` (and `DELIVERY_REVIEW_MIN_DAYS`) | no decision changes: every pack is 28 days or less except pack 10 (60, already rejected). Try 25 to see packs 04, 06, 09 move to review/reject |
| Payment window 30-60 days | `PAYMENT_MIN_ACCEPTABLE_DAYS`, `PAYMENT_MAX_ACCEPTABLE_DAYS` | |
| Quantity tolerance 5% | `QTY_REVIEW_THRESHOLD_RATIO` | |
| Min penalty 0.5%/week | `PENALTY_MIN_RATE_PCT` | |
| Quote validity 30 days | `VALIDITY_MIN_DAYS` | |

After any change: `python -m pytest tests/ -q -m "not integration"`. Some boundary tests are expected to fail because they encode the old threshold; say so and update them. Check the web demo with `/api/check?summary=1` (it compares against `evaluation/ground_truth.json`, so a changed threshold will show mismatches, which is the correct signal).

New rule: add a function in `src/rule_engine.py`, register it in `evaluate_all_rules` and `GLOBAL_RULES`, add a test in `tests/test_rule_engine.py`.
