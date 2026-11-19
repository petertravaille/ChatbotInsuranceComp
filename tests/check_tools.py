"""Offline checks of the database tools against the raw data (no API key needed).

Run:  python tests/check_tools.py
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from tools import _connect, execute_tool, get_member_overview  # noqa: E402

con = _connect()
problems = []
for (mid,) in con.execute("SELECT member_id FROM members"):
    for year in (2025, 2026):
        o = get_member_overview(con, mid, year)
        d, p = o["deductible"], o["physiotherapy"]
        if not 0 <= d["used_eur"] <= d["total_eur"]:
            problems.append(f"{mid} {year}: deductible used {d['used_eur']} of {d['total_eur']}")
        if p["max_visits_per_year_supplementary"] and \
                p["supplementary_visits_remaining"] != max(0, p["max_visits_per_year_supplementary"] - p["visits_claimed_total"]):
            problems.append(f"{mid} {year}: physio remaining is off")

# Members only ever see their own data; employees can't write.
assert json.loads(execute_tool("get_member_overview", {"member_id": "M100002"}, "member", "M100001")[0])["member"]["member_id"] == "M100001"
# A member asking for another member's visit date only ever gets their own claims (or none).
other = con.execute("SELECT service_date FROM claims WHERE member_id = 'M100002' AND service_date NOT IN "
                    "(SELECT service_date FROM claims WHERE member_id = 'M100001') LIMIT 1").fetchone()[0]
assert execute_tool("get_claim", {"member_id": "M100002", "visit_date": other}, "member", "M100001")[1]
# Claim ids never appear in what the member-facing tools return.
for tool, args in (("search_claims", {}), ("physio_sessions", {}), ("get_member_overview", {})):
    out = execute_tool(tool, args, "member", "M100015")[0]
    assert "claim_id" not in out and not re.search(r"\bC\d{6}\b", out), tool
assert execute_tool("search_claims", {"member_id": "M999999"}, "employee")[1]  # unknown member is an error
assert execute_tool("run_sql", {"query": "SELECT 1"}, "member", "M100001")[1]
assert execute_tool("run_sql", {"query": "DELETE FROM claims"}, "employee")[1]
assert execute_tool("run_sql", {"query": "SELECT 1; DROP TABLE claims"}, "employee")[1]

print("\n".join(problems) or "All tool checks passed.")
sys.exit(1 if problems else 0)
