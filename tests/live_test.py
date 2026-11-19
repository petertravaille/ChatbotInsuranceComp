"""Live test: ask the chatbot all 20 test questions, several times each, and check the answers.

Needs ANTHROPIC_API_KEY. Run:
    python tests/live_test.py            # every question 3 times
    python tests/live_test.py --runs 1 --only 4 9
Writes tests/results/live_test_results.json and prints a summary.
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Talk to the real Claude API with the chatbot's own key, even inside hosted tools that point
# ANTHROPIC_BASE_URL somewhere else for themselves.
os.environ.pop("ANTHROPIC_BASE_URL", None)
if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CHATBOT_ANTHROPIC_API_KEY")):
    sys.exit("No API key: set ANTHROPIC_API_KEY (or CHATBOT_ANTHROPIC_API_KEY) first.")
sys.path.insert(0, str(HERE.parent / "app"))
sys.path.insert(0, str(HERE))
from chatbot import CLAIM_ID, ChatSession  # noqa: E402
from expected import build  # noqa: E402

PHYSIO = re.compile(r"physio", re.I)


def table_header(answer):
    """Header rows of every Markdown table in the answer, as lists of lower-case cells."""
    lines = answer.splitlines()
    out = []
    for i, ln in enumerate(lines[:-1]):
        if ln.strip().startswith("|") and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            out.append([c.strip().lower() for c in ln.strip().strip("|").split("|")])
    return out


def rule_checks(t, answer):
    """Pete's house rules, checked on every answer."""
    checks = [("no internal claim ids (Cxxxxxx)", not CLAIM_ID.search(answer))]
    heads = table_header(answer)
    claim_tables = [h for h in heads if "status" in h]
    if claim_tables:
        ok = all("eigen risico" in h and h.index("eigen risico") == h.index("status") + 1 for h in claim_tables)
        checks.append(("claim table has Eigen risico next to Status", ok))
    if PHYSIO.search(t["question"]) and t["kind"] != "Privacy" and t["as"] != "employee":
        ok = any(all(c in h for c in ("provider", "location", "status")) for h in heads)
        checks.append(("full physio table with provider, location and status", ok))
    return checks


def run_one(t, attempt):
    role = "employee" if t["as"] == "employee" else "member"
    name = None if role == "employee" else t["as_name"].rsplit(" (", 1)[0]
    chat = ChatSession(role, None if role == "employee" else t["as"], name)
    started = time.time()
    for tries in range(4):
        try:
            answer, tools = chat.ask(t["question"])
            break
        except Exception as e:  # network hiccup or overload: back off and retry
            if tries == 3:
                answer, tools = f"ERROR: {e}", []
            time.sleep(5 * (tries + 1))
    checks = [(label, bool(fn(answer))) for label, fn in t["checks"]] + rule_checks(t, answer)
    return {"n": t["n"], "attempt": attempt, "answer": answer, "seconds": round(time.time() - started, 1),
            "tools": [x["tool"] for x in tools], "checks": [{"check": c, "ok": ok} for c, ok in checks],
            "passed": all(ok for _, ok in checks)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--out", default=str(HERE / "results" / "live_test_results.json"))
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    tests = [t for t in build() if not args.only or t["n"] in args.only]
    jobs = [(t, a) for t in tests for a in range(1, args.runs + 1)]
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda j: run_one(*j), jobs))
    out = []
    for t in tests:
        runs = [r for r in results if r["n"] == t["n"]]
        out.append({k: t[k] for k in ("n", "kind", "as", "as_name", "question", "expected")} | {"runs": runs})
        print(f"{t['n']:>2}. {'PASS' if all(r['passed'] for r in runs) else 'FAIL'} "
              f"({sum(r['passed'] for r in runs)}/{len(runs)})  {t['question']}")
        for r in runs:
            for c in r["checks"]:
                if not c["ok"]:
                    print(f"      run {r['attempt']}: failed check: {c['check']}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"date": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                                          "runs_per_question": args.runs, "tests": out}, indent=2))
    passed = sum(all(r["passed"] for r in x["runs"]) for x in out)
    print(f"\n{passed}/{len(out)} questions passed on every run. Results: {args.out}")


if __name__ == "__main__":
    main()
