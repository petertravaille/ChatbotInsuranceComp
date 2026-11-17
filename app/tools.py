"""Read-only database tools the chatbot can call.

The claims data and the policy data live in two separate SQLite databases.
Every connection is opened read-only, and members can only ever see their own
records: the member id is taken from the logged-in session, never from the model.
"""

import json
import re
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CLAIMS_DB = DATA_DIR / "claims.db"
POLICIES_DB = DATA_DIR / "policies.db"
CURRENT_YEAR = 2026
MAX_ROWS = 200


def _connect():
    """Claims DB as `main`, policy DB attached as `pol`, both read-only."""
    con = sqlite3.connect(f"file:{CLAIMS_DB}?mode=ro", uri=True)
    con.execute("ATTACH DATABASE ? AS pol", (f"file:{POLICIES_DB}?mode=ro",))
    con.row_factory = sqlite3.Row
    return con


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


# --------------------------------------------------------------------------
# Tool implementations
# --------------------------------------------------------------------------

def get_member_overview(con, member_id, year=CURRENT_YEAR):
    member = con.execute("SELECT * FROM members WHERE member_id = ?", (member_id,)).fetchone()
    if not member:
        return {"error": f"No member found with id {member_id}."}
    mp = con.execute("SELECT * FROM pol.member_policies WHERE member_id = ? AND year = ?",
                     (member_id, year)).fetchone()
    if not mp:
        return {"member": dict(member), "error": f"No policy found for {year}."}
    mp = dict(mp)
    ded_total = con.execute("SELECT mandatory_deductible_eur FROM pol.deductible_years WHERE year = ?",
                            (year,)).fetchone()[0] + mp["voluntary_deductible_eur"]
    ded_used = con.execute("SELECT COALESCE(SUM(deductible_applied_eur),0) FROM claims "
                           "WHERE member_id = ? AND substr(service_date,1,4) = ?",
                           (member_id, str(year))).fetchone()[0]
    policy_ids = [p for p in (mp["basic_policy_id"], mp["supplementary_policy_id"], mp["dental_policy_id"]) if p]
    names = {r["policy_id"]: r["product_name"] for r in con.execute(
        f"SELECT policy_id, product_name FROM pol.policies WHERE policy_id IN ({','.join('?' * len(policy_ids))})",
        policy_ids)}
    physio_max = None
    if mp["supplementary_policy_id"]:
        r = con.execute("SELECT max_visits_per_year FROM pol.coverage_rules WHERE policy_id = ? "
                        "AND care_category = 'physiotherapy'", (mp["supplementary_policy_id"],)).fetchone()
        physio_max = r[0] if r else None
    pv = con.execute(
        "SELECT COALESCE(SUM(physio_visits),0) AS claimed, "
        "COALESCE(SUM(CASE WHEN status = 'pending' THEN physio_visits END),0) AS pending, "
        "COALESCE(SUM(CASE WHEN status = 'denied' THEN physio_visits END),0) AS denied "
        "FROM claims WHERE member_id = ? AND care_category = 'physiotherapy' AND substr(service_date,1,4) = ?",
        (member_id, str(year))).fetchone()
    claimed = pv["claimed"]
    chronic = bool(mp["chronic_physio_indication"])
    by_status = _rows(con.execute(
        "SELECT status, COUNT(*) AS claims, ROUND(SUM(claimed_amount_eur),2) AS claimed_eur, "
        "ROUND(SUM(COALESCE(reimbursed_amount_eur,0)),2) AS reimbursed_eur FROM claims "
        "WHERE member_id = ? AND substr(service_date,1,4) = ? GROUP BY status", (member_id, str(year))))
    return {
        "member": dict(member),
        "year": year,
        "policies": {pid: names.get(pid) for pid in policy_ids},
        "chronic_physio_indication": chronic,
        "deductible": {
            "mandatory_eur": ded_total - mp["voluntary_deductible_eur"],
            "voluntary_eur": mp["voluntary_deductible_eur"],
            "total_eur": ded_total,
            "used_eur": round(ded_used, 2),
            "remaining_eur": round(ded_total - ded_used, 2),
        },
        "physiotherapy": {
            "max_visits_per_year_supplementary": physio_max,
            "visits_claimed_total": claimed,
            "visits_in_pending_claims": pv["pending"],
            "visits_in_denied_claims": pv["denied"],
            "supplementary_visits_remaining": max(0, physio_max - claimed) if physio_max else 0,
            "note": " ".join(filter(None, [
                ("Remaining visits count claims still pending as if they will be paid."
                 if pv["pending"] and physio_max else None),
                (f"Chronic indication: basic insurance covers sessions from the 21st per year "
                 f"({max(0, 20 - claimed)} more session(s) before that starts)." if chronic else None),
                (None if physio_max or chronic else
                 "No supplementary package with physiotherapy, so physiotherapy is not covered."),
            ])) or None,
        },
        "claims_by_status": by_status,
    }


def search_claims(con, member_id=None, year=None, care_category=None, status=None, limit=50):
    sql = ("SELECT c.claim_id, c.member_id, c.service_date, c.care_category, c.status, c.physio_visits, "
           "c.claimed_amount_eur, c.reimbursed_amount_eur, c.deductible_applied_eur, c.member_pays_eur, "
           "c.decision_reason, p.name AS provider FROM claims c JOIN providers p USING (provider_id) WHERE 1=1")
    args = []
    for col, val in (("c.member_id", member_id), ("c.care_category", care_category), ("c.status", status)):
        if val:
            sql += f" AND {col} = ?"
            args.append(val)
    if year:
        sql += " AND substr(c.service_date,1,4) = ?"
        args.append(str(year))
    sql += " ORDER BY c.service_date DESC LIMIT ?"
    args.append(min(int(limit or 50), MAX_ROWS))
    rows = _rows(con.execute(sql, args))
    return {"count": len(rows), "claims": rows}


def get_claim(con, claim_id, member_id=None):
    c = con.execute("SELECT c.*, p.name AS provider_name, p.contracted AS provider_contracted "
                    "FROM claims c JOIN providers p USING (provider_id) WHERE claim_id = ?",
                    (claim_id,)).fetchone()
    if not c or (member_id and c["member_id"] != member_id):
        return {"error": f"Claim {claim_id} not found."}
    lines = _rows(con.execute("SELECT treatment_code, description, diagnosis_code, diagnosis, quantity, "
                              "unit_price_eur, amount_eur FROM claim_lines WHERE claim_id = ?", (claim_id,)))
    return {"claim": dict(c), "lines": lines}


def get_policy_terms(con, policy_id=None, care_category=None):
    sql = ("SELECT p.policy_id, p.product_name, p.product_type, p.monthly_premium_eur, r.care_category, "
           "r.category_name, r.covered_pct, r.annual_limit_eur, r.max_visits_per_year, r.requires_preauth, "
           "r.deductible_applies, r.notes FROM pol.policies p LEFT JOIN pol.coverage_rules r USING (policy_id) WHERE 1=1")
    args = []
    if policy_id:
        sql += " AND p.policy_id = ?"
        args.append(policy_id)
    if care_category:
        sql += " AND r.care_category = ?"
        args.append(care_category)
    deductibles = _rows(con.execute("SELECT * FROM pol.deductible_years"))
    return {"rules": _rows(con.execute(sql, args)), "deductible_years": deductibles}


def check_coverage(con, member_id, care_category, year=CURRENT_YEAR, estimated_cost_eur=None, planned_visits=None):
    """Advice helper: what a member's policies pay for a category, and what is left this year."""
    mp = con.execute("SELECT * FROM pol.member_policies WHERE member_id = ? AND year = ?",
                     (member_id, year)).fetchone()
    if not mp:
        return {"error": f"No policy found for member {member_id} in {year}."}
    policy_ids = [p for p in (mp["basic_policy_id"], mp["supplementary_policy_id"], mp["dental_policy_id"]) if p]
    rules = _rows(con.execute(
        f"SELECT * FROM pol.coverage_rules WHERE care_category = ? AND policy_id IN ({','.join('?' * len(policy_ids))})",
        [care_category, *policy_ids]))
    used = con.execute(
        "SELECT COUNT(*) AS claims, COALESCE(SUM(physio_visits),0) AS visits, "
        "ROUND(COALESCE(SUM(reimbursed_amount_eur),0),2) AS reimbursed_eur FROM claims "
        "WHERE member_id = ? AND care_category = ? AND substr(service_date,1,4) = ?",
        (member_id, care_category, str(year))).fetchone()
    overview = get_member_overview(con, member_id, year)
    result = {
        "member_id": member_id, "year": year, "care_category": care_category,
        "applicable_rules": rules,
        "used_this_year": dict(used),
        "deductible_remaining_eur": overview["deductible"]["remaining_eur"],
        "chronic_physio_indication": bool(mp["chronic_physio_indication"]),
    }
    if not rules:
        result["verdict"] = "Not covered by any of the member's policies this year."
    for r in rules:
        if r["max_visits_per_year"]:
            r["visits_remaining"] = max(0, r["max_visits_per_year"] - used["visits"])
        if r["annual_limit_eur"]:
            r["money_remaining_eur"] = round(max(0.0, r["annual_limit_eur"] - used["reimbursed_eur"]), 2)
    if planned_visits is not None:
        result["planned_visits"] = planned_visits
    if estimated_cost_eur is not None:
        result["estimated_cost_eur"] = estimated_cost_eur
    return result


def find_members(con, name=None, city=None, limit=20):
    sql, args = "SELECT member_id, first_name, last_name, date_of_birth, city FROM members WHERE 1=1", []
    if name:
        sql += " AND (first_name || ' ' || last_name) LIKE ?"
        args.append(f"%{name}%")
    if city:
        sql += " AND city = ?"
        args.append(city)
    sql += " LIMIT ?"
    args.append(min(int(limit or 20), MAX_ROWS))
    return {"members": _rows(con.execute(sql, args))}


_SELECT_ONLY = re.compile(r"^\s*(WITH|SELECT)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|REPLACE|VACUUM)\b",
                        re.IGNORECASE)


def run_sql(con, query):
    if not _SELECT_ONLY.match(query) or _FORBIDDEN.search(query) or ";" in query.strip().rstrip(";"):
        return {"error": "Only a single read-only SELECT query is allowed."}
    cur = con.execute(query)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchmany(MAX_ROWS)
    return {"columns": cols, "rows": [list(r) for r in rows], "truncated": len(rows) == MAX_ROWS}


SCHEMA_HELP = """Tables in the claims database (main):
  members(member_id, first_name, last_name, date_of_birth, city, email)
  providers(provider_id, name, provider_type, city, contracted)
  claims(claim_id, member_id, provider_id, care_category, service_date, submitted_date, processed_date,
         physio_visits, preauth_reference, status, claimed_amount_eur, reimbursed_amount_eur,
         deductible_applied_eur, member_pays_eur, covered_by, decision_reason)
  claim_lines(line_id, claim_id, treatment_code, description, diagnosis_code, diagnosis, quantity,
              unit_price_eur, amount_eur)
  views: member_physio_visits(member_id, year, physio_visits_claimed, physio_visits_assessed),
         member_deductible_used(member_id, year, deductible_used_eur)
Tables in the policy database (prefix pol.):
  pol.policies(policy_id, product_name, product_type, monthly_premium_eur, description)
  pol.coverage_rules(policy_id, care_category, category_name, covered_pct, annual_limit_eur,
                     max_visits_per_year, requires_preauth, deductible_applies, notes)
  pol.deductible_years(year, mandatory_deductible_eur, notes)
  pol.member_policies(member_id, year, basic_policy_id, supplementary_policy_id, dental_policy_id,
                      voluntary_deductible_eur, chronic_physio_indication)
Dates are ISO text (YYYY-MM-DD). status is one of paid, partially_paid, denied, pending.
care_category is one of gp, specialist, imaging, pharmacy, mental_health, physiotherapy, dental, glasses, alternative."""

CATEGORY_ENUM = ["gp", "specialist", "imaging", "pharmacy", "mental_health", "physiotherapy",
                 "dental", "glasses", "alternative"]

# --------------------------------------------------------------------------
# Tool definitions for Claude
# --------------------------------------------------------------------------

_MEMBER_ID = {"type": "string", "description": "Member id, e.g. M100015."}
_YEAR = {"type": "integer", "description": f"Calendar year (2025 or 2026). Defaults to {CURRENT_YEAR}."}


def _tool(name, description, props, required):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, "required": required}}


def tool_definitions(role):
    """Members get their own data only (member_id is filled in by the server); employees get everything."""
    member_scoped = role == "member"
    mid = {} if member_scoped else {"member_id": _MEMBER_ID}
    mid_req = [] if member_scoped else ["member_id"]
    tools = [
        _tool("get_member_overview",
              "Overview of a member for one year: their policies, deductible (eigen risico) used and remaining, "
              "physiotherapy visits used and the yearly maximum, and claim totals by status.",
              {**mid, "year": _YEAR}, mid_req),
        _tool("search_claims",
              "List claims, newest first, filtered by year, care category and status. Returns amounts, "
              "reimbursements, deductible applied, physio visits and the decision reason.",
              {**mid, "year": _YEAR,
               "care_category": {"type": "string", "enum": CATEGORY_ENUM},
               "status": {"type": "string", "enum": ["paid", "partially_paid", "denied", "pending"]},
               "limit": {"type": "integer", "description": "Max rows (default 50)."}},
              mid_req),
        _tool("get_claim", "Full details of one claim including its treatment lines and diagnosis.",
              {"claim_id": {"type": "string", "description": "Claim id, e.g. C100123."}}, ["claim_id"]),
        _tool("get_policy_terms",
              "Coverage rules from the policy database: what each product covers, percentages, money limits, "
              "max physiotherapy visits per year, prior authorisation and whether the deductible applies. "
              "Leave both filters out to list everything.",
              {"policy_id": {"type": "string"},
               "care_category": {"type": "string", "enum": CATEGORY_ENUM}},
              []),
        _tool("check_coverage",
              "Advice helper: for a member and care category, return the applicable coverage rules from their "
              "policies, what they already used this year (visits, euros), remaining visits/limits and "
              "remaining deductible. Use this before advising on a planned treatment.",
              {**mid, "care_category": {"type": "string", "enum": CATEGORY_ENUM}, "year": _YEAR,
               "planned_visits": {"type": "integer"},
               "estimated_cost_eur": {"type": "number"}},
              mid_req + ["care_category"]),
    ]
    if not member_scoped:
        tools += [
            _tool("find_members", "Find members by (part of) their name and/or city.",
                  {"name": {"type": "string"}, "city": {"type": "string"},
                   "limit": {"type": "integer"}}, []),
            _tool("run_sql",
                  "Run one read-only SQL SELECT across both databases for questions the other tools can't "
                  "answer (counts, totals, trends). Schema:\n" + SCHEMA_HELP,
                  {"query": {"type": "string"}}, ["query"]),
        ]
    return tools


_IMPL = {
    "get_member_overview": get_member_overview,
    "search_claims": search_claims,
    "get_claim": get_claim,
    "get_policy_terms": get_policy_terms,
    "check_coverage": check_coverage,
    "find_members": find_members,
    "run_sql": run_sql,
}


def execute_tool(name, args, role, member_id=None):
    """Run a tool and return (json_text, is_error). Members are always pinned to their own id."""
    allowed = {t["name"] for t in tool_definitions(role)}
    if name not in allowed:
        return json.dumps({"error": f"Tool {name} is not available."}), True
    args = {k: v for k, v in dict(args).items() if v is not None}
    if role == "member":
        if name in ("get_member_overview", "search_claims", "check_coverage", "get_claim"):
            args["member_id"] = member_id
    con = _connect()
    try:
        result = _IMPL[name](con, **args)
        return json.dumps(result, default=str), "error" in result
    except (sqlite3.Error, TypeError, ValueError) as e:
        return json.dumps({"error": str(e)}), True
    finally:
        con.close()


def list_members_for_login(limit=40):
    con = _connect()
    try:
        return _rows(con.execute("SELECT member_id, first_name, last_name, city FROM members "
                                 "ORDER BY member_id LIMIT ?", (limit,)))
    finally:
        con.close()
