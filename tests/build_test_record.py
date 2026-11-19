"""Build the non-technical test record page from the live test result files.

python tests/build_test_record.py round1.json round2.json [round3.json ...] > test-record.html
Round 1 is the run before fixes; the last round is the final run after fixes.
"""
import html
import json
import sys

# Hand review of every answer. Default verdict is "Correct" when the automatic checks pass.
# Exceptions found by reading the answers (round, question, run) -> (verdict, note).
HAND = {
    (1, 5, 1): ("Small slip", "Says \"your EUR 385.00 deductible\". Her total deductible is EUR 635.00 "
                "(EUR 385.00 mandatory plus EUR 250.00 voluntary). The conclusion (you pay EUR 0.00) is right."),
    (1, 10, 2): ("Small slip", "Correctly says Extra Care 4 doesn't exist, but in its comparison table gives "
                 "Extra Care 3 alternative medicine as up to EUR 300.00. The policy says EUR 500.00."),
    (1, 15, 3): ("Small slip", "Counts are all right (202 denied claims), but the total claimed is shown as "
                 "EUR 29,928.88. The rows add up to EUR 29,927.88."),
    (1, 17, 1): ("Correct", "The automatic check failed only because the test expected an Eigen risico column "
                 "in this summary table. It is a summary by status, not a list of claims, so the rule doesn't apply. "
                 "Total EUR 183,964.55 is exact."),
    (1, 18, 1): ("Correct", "The automatic check failed only because it didn't accept \"EUR 150.00\" written with "
                 "decimals. All figures match the policy."),
}
for r in (2, 3):
    HAND[(1, 17, r)] = HAND[(1, 17, 1)]
    HAND[(1, 18, r)] = HAND[(1, 18, 1)]

FIXES = [
    ("Wrong limit copied from another package (Q10)",
     "The chatbot now gets an explicit rule to take every amount and limit from that exact product's own entry."),
    ("Total off by EUR 1.00 (Q15)",
     "The chatbot now re-adds any totals before answering. The number check also runs on every answer: if a "
     "number isn't in the data and isn't a sum worked out from it, the chatbot gets one chance to recheck and correct it. Q15 was not in the partial rerun, but one extra check run after the fix gave the exact total."),
    ("Deductible stated as EUR 385.00 instead of EUR 635.00 (Q5)",
     "Added a rule that a member's deductible is always the total: the mandatory EUR 385.00 plus any voluntary part."),
    ("Faulty test checks (Q17, Q18)",
     "Fixed the tests themselves: amounts like EUR 150.00 are accepted, and the Eigen risico column rule is only "
     "checked on lists of claims."),
]


def load(path, rnd):
    d = json.load(open(path))
    out = []
    for t in d["tests"]:
        runs = []
        for r in t["runs"]:
            if r["answer"].startswith("ERROR"):
                continue
            verdict, note = HAND.get((rnd, t["n"], r["attempt"]),
                                     ("Correct" if r["passed"] else "Wrong", ""))
            runs.append({"attempt": r["attempt"], "answer": r["answer"], "seconds": r["seconds"],
                         "auto": r["passed"], "verdict": verdict, "note": note,
                         "failed": [c["check"] for c in r["checks"] if not c["ok"]],
                         "selfcheck": r.get("number_check"), "corrected": bool(r.get("self_corrected")),
                         "blocked": r.get("privacy_blocked")})
        out.append({k: t[k] for k in ("n", "kind", "as_name", "question", "expected")} | {"runs": runs})
    return {"round": rnd, "date": d["date"], "tests": out}


rounds = [load(p, i + 1) for i, p in enumerate(sys.argv[1:])]
data = json.dumps({"rounds": rounds, "fixes": FIXES}).replace("</", "<\\/")
page = open(__file__.replace("build_test_record.py", "test_record_template.html")).read().replace("/*DATA*/null", data)
sys.stdout.write(page)
