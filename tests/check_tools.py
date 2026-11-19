"""Offline checks of the database tools against the raw data (no API key needed).

Run:  python tests/check_tools.py
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from guards import number_check, privacy_leak  # noqa: E402
from tools import _connect, claims_table, execute_tool, get_member_overview  # noqa: E402

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

# Members don't get the employee-only tools, even when asked for by name.
for tool in ("find_members", "physio_usage", "run_sql"):
    assert execute_tool(tool, {}, "member", "M100001")[1], tool
# Every member lookup is pinned to the signed-in member.
assert json.loads(execute_tool("physio_sessions", {"member_id": "M100002"}, "member", "M100015")[0])["member"] == \
    json.loads(execute_tool("physio_sessions", {}, "member", "M100015")[0])["member"]
# Physio usage (employees) agrees with the per-member overview.
usage = json.loads(execute_tool("physio_usage", {"only_limit_reached": True}, "employee")[0])
assert usage["members"] and all(r["max_visits"] and r["visits_claimed"] >= r["max_visits"] for r in usage["members"])

# The fixed claims table on the page: matches the raw data, and never shows claim ids.
tbl = claims_table(con, "M100015", 2026)
n = con.execute("SELECT COUNT(*) FROM claims WHERE member_id='M100015' AND service_date LIKE '2026%'").fetchone()[0]
assert len(tbl["claims"]) == n and n > 0
assert "claim_id" not in json.dumps(tbl) and not re.search(r"\bC\d{6}\b", json.dumps(tbl))
assert tbl["summary"]["eigen_risico_used_eur"] == get_member_overview(con, "M100015", 2026)["deductible"]["used_eur"]

# Output guards.
other_name = " ".join(con.execute("SELECT first_name, last_name FROM members WHERE member_id='M100002'").fetchone())
own_name = " ".join(con.execute("SELECT first_name, last_name FROM members WHERE member_id='M100015'").fetchone())
assert privacy_leak(f"{other_name} used 12 sessions.", "M100015") == other_name
assert privacy_leak("Member M100002 used 12 sessions.", "M100015") == "M100002"
assert privacy_leak(f"Hi {own_name}, you used 12 sessions (M100015).", "M100015") is None
data = execute_tool("physio_sessions", {}, "member", "M100015")[0]
ps = json.loads(data)
good = f"You used {ps['total_sessions']} sessions on 3 Mar 2026; EUR {ps['sessions'][0]['claimed_eur']:.2f} claimed."
assert number_check(good, [data])["ok"], number_check(good, [data])
bad = number_check("You have 4,321.17 left and used 987 sessions.", [data])
assert not bad["ok"] and 4321.17 in bad["missing"] and 987 in bad["missing"], bad
assert number_check("No numbers here.", [data]) is None

print("\n".join(problems) or "All tool checks passed.")
sys.exit(1 if problems else 0)
