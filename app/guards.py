"""Checks run on every answer before it is shown.

- privacy_leak:     in member mode, block an answer that names another member or member id.
- number_check:     flag numbers in the answer that don't come from the data the tools returned.
"""

import json
import re

from tools import _connect

BLOCKED_ANSWER = "I can only share information about your own insurance, so I can't answer that."

_members_cache = None


def _members():
    global _members_cache
    if _members_cache is None:
        con = _connect()
        try:
            _members_cache = [(r["member_id"], f"{r['first_name']} {r['last_name']}")
                              for r in con.execute("SELECT member_id, first_name, last_name FROM members")]
        finally:
            con.close()
    return _members_cache


def privacy_leak(text, member_id):
    """Return the other member's name or id if a member's answer mentions one, else None."""
    own_name = next((n for mid, n in _members() if mid == member_id), None)
    for mid, name in _members():
        if mid == member_id:
            continue
        if re.search(rf"\b{mid}\b", text, re.IGNORECASE):
            return mid
        if name != own_name and re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            return name
    return None


# ---------------------------------------------------------------------------
# Number check
# ---------------------------------------------------------------------------

_MONTH = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"


def numbers_in(text):
    """Numbers in an answer, skipping dates, years, ids and ordinals ('21st')."""
    t = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text)
    t = re.sub(rf"\b\d{{1,2}}\s+{_MONTH}(\s+\d{{4}})?", " ", t, flags=re.IGNORECASE)
    t = re.sub(rf"\b{_MONTH}\s+\d{{1,2}}(,?\s+\d{{4}})?", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(19|20)\d{2}\b", " ", t)
    t = re.sub(r"\b[A-Z]{1,4}-\d+\b", " ", t)        # policy ids like SUP-2
    t = re.sub(r"\b[MC]\d{6}\b", " ", t)               # member / claim ids
    t = re.sub(r"\b\d+(st|nd|rd|th)\b", " ", t)
    out = []
    for m in re.finditer(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?", t):
        raw = m.group(0)
        if re.fullmatch(r"\d{1,3}([.,]\d{3})+([.,]\d{1,2})?", raw):
            dec = re.search(r"[.,](\d{1,2})$", raw)
            int_part = raw[:-len(dec.group(0))] if dec else raw
            raw = re.sub(r"[.,]", "", int_part) + ("." + dec.group(1) if dec else "")
        else:
            raw = raw.replace(",", ".")
        out.append(float(raw))
    return out


def column_totals(answer):
    """Sums of each numeric column in the answer's Markdown tables (data rows only, not a bold total row).

    A total that the answer adds up itself (for example "202 denied claims" or a **Total** row) is then
    accepted as worked out from the data, as long as the rows it adds up were found in the data.
    """
    totals = set()
    rows = [ln.strip().strip("|").split("|") for ln in answer.splitlines() if ln.strip().startswith("|")]
    table = []
    for row in rows + [None]:
        if row is not None and not all(re.fullmatch(r"\s*:?-{2,}:?\s*", c) for c in row):
            if not any("**" in c for c in row):
                table.append(row)
            continue
        if row is None or table:
            for col in zip(*[r for r in table if len(r) == len(table[0])]):
                vals = [numbers_in(c) for c in col]
                if any(vals):
                    totals.add(round(sum(v[0] for v in vals if v) * 100))
            if row is None:
                break
        table = []
    return totals


def number_check(answer, tool_outputs):
    """Compare the numbers in an answer with the tool data.

    Returns None when there is nothing to check, else a dict:
      ok:       True when every number was found
      direct:   numbers that appear in the data
      derived:  numbers that are a sum or difference of two numbers in the data
      missing:  numbers not found in the data (possible mistakes)
      message:  one line to show under the answer
    """
    if not tool_outputs:
        return None
    nums = sorted({n for n in numbers_in(answer) if n > 3 or n != int(n)})
    if not nums:
        return None
    found = set()
    for out in tool_outputs:
        for m in re.finditer(r"-?\d+(?:\.\d+)?", out if isinstance(out, str) else json.dumps(out)):
            found.add(round(float(m.group(0)) * 100))
    base = list(found)[:400]
    derived = {a + b for a in base for b in base} | {a - b for a in base for b in base} | column_totals(answer)
    direct, calc, missing = [], [], []
    for n in nums:
        k = round(n * 100)
        (direct if k in found else calc if k in derived or -k in derived else missing).append(n)
    fmt = ", ".join(f"{n:g}" for n in missing)
    if not missing:
        msg = f"Number check: all {len(direct) + len(calc)} numbers come from the data"
        msg += f" ({len(calc)} worked out from it)." if calc else "."
    else:
        msg = f"Number check: {fmt} not found in the data that was looked up. Check these against the claims table."
    return {"ok": not missing, "direct": direct, "derived": calc, "missing": missing, "message": msg}
