"""Generate the synthetic data for the PT Code Chatbot prototype.

Creates two separate SQLite databases, modelled on the Dutch health insurance
system (basic insurance with a yearly mandatory deductible, plus optional
supplementary packages):

  policies.db  - insurance products, coverage rules (incl. max physio visits
                 per year), deductibles, and which member holds which policy.
  claims.db    - members, providers, claims and claim lines (incl. the number
                 of physiotherapy visits per claim).

Claims are adjudicated against the policy rules while generating, so the
statuses, reimbursements and deductible use are consistent with the policies.
All names and records are fake. Run:  python data/generate_data.py
"""

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SEED = 42
TODAY = date(2026, 11, 17)
YEARS = (2025, 2026)
N_MEMBERS = 150
MANDATORY_DEDUCTIBLE = {2025: 385.0, 2026: 385.0}
CHRONIC_BASIC_FROM_VISIT = 21  # basic insurance covers chronic physio from the 21st session

DATA_DIR = Path(__file__).resolve().parent
rng = random.Random(SEED)

# --------------------------------------------------------------------------
# Policy catalogue
# --------------------------------------------------------------------------
POLICIES = [
    # policy_id, name, type, monthly_premium, description
    ("BAS-N", "Basic Care In-Kind", "basic", 142.50,
     "Mandatory basic insurance (basisverzekering). Full cover at contracted providers."),
    ("BAS-R", "Basic Care Free Choice", "basic", 151.75,
     "Mandatory basic insurance (basisverzekering) with free choice of provider (restitution)."),
    ("SUP-1", "Extra Care 1", "supplementary", 14.95,
     "Entry supplementary package: physiotherapy and glasses."),
    ("SUP-2", "Extra Care 2", "supplementary", 29.50,
     "Mid supplementary package: more physiotherapy, glasses, alternative medicine."),
    ("SUP-3", "Extra Care 3", "supplementary", 49.95,
     "Top supplementary package: generous physiotherapy, glasses, alternative medicine."),
    ("DEN-1", "Dental Plus", "dental", 18.25,
     "Supplementary dental cover for adults."),
]

# care categories with display names
CATEGORIES = {
    "gp": "GP consultation",
    "specialist": "Hospital / medical specialist care",
    "imaging": "Diagnostic imaging (MRI / CT)",
    "pharmacy": "Prescription medication",
    "mental_health": "Mental health care",
    "physiotherapy": "Physiotherapy",
    "dental": "Dental care (adult)",
    "glasses": "Glasses and contact lenses",
    "alternative": "Alternative medicine",
}

# policy_id, category, covered_pct, annual_limit_eur, max_visits_per_year,
# requires_preauth, deductible_applies, notes
BASIC_RULES = [
    ("gp", 100, None, None, 0, 0, "GP care is exempt from the deductible."),
    ("specialist", 100, None, None, 0, 1, "Requires a GP referral."),
    ("imaging", 100, None, None, 1, 1, "MRI/CT requires prior authorisation from the insurer."),
    ("pharmacy", 100, None, None, 0, 1, "Covered medication on the national list."),
    ("mental_health", 100, None, None, 0, 1, "Basic mental health care with a GP referral."),
    ("physiotherapy", 100, None, None, 0, 1,
     "Only for conditions on the chronic list, from the 21st session per year. "
     "Sessions 1-20 must be covered by a supplementary package or paid by the member."),
]
RULES = {
    "BAS-N": BASIC_RULES,
    "BAS-R": BASIC_RULES,
    "SUP-1": [
        ("physiotherapy", 100, None, 9, 0, 0, "Up to 9 sessions per calendar year."),
        ("glasses", 100, 100.0, None, 0, 0, "Up to EUR 100 per calendar year."),
    ],
    "SUP-2": [
        ("physiotherapy", 100, None, 18, 0, 0, "Up to 18 sessions per calendar year."),
        ("glasses", 100, 150.0, None, 0, 0, "Up to EUR 150 per calendar year."),
        ("alternative", 75, 300.0, None, 0, 0, "75% up to EUR 300 per year, registered therapists only."),
    ],
    "SUP-3": [
        ("physiotherapy", 100, None, 27, 0, 0, "Up to 27 sessions per calendar year."),
        ("glasses", 100, 250.0, None, 0, 0, "Up to EUR 250 per calendar year."),
        ("alternative", 80, 500.0, None, 0, 0, "80% up to EUR 500 per year, registered therapists only."),
    ],
    "DEN-1": [
        ("dental", 75, 500.0, None, 0, 0, "75% of adult dental costs up to EUR 500 per year."),
    ],
}

# --------------------------------------------------------------------------
# Fake people and providers
# --------------------------------------------------------------------------
FIRST = ["Daan", "Sem", "Lucas", "Noah", "Finn", "Levi", "Milan", "Bram", "Ruben", "Thijs",
         "Emma", "Julia", "Sophie", "Tess", "Anna", "Saar", "Fleur", "Lotte", "Eva", "Noor",
         "Mohamed", "Yusuf", "Fatima", "Amira", "Priya", "Chen", "Mila", "Jesse", "Lars", "Sanne",
         "Koen", "Maaike", "Pieter", "Ingrid", "Henk", "Wilma", "Joost", "Marieke", "Ahmed", "Elif"]
LAST = ["de Jong", "Jansen", "de Vries", "van den Berg", "van Dijk", "Bakker", "Janssen",
        "Visser", "Smit", "Meijer", "de Boer", "Mulder", "de Groot", "Bos", "Vos", "Peters",
        "Hendriks", "van Leeuwen", "Dekker", "Brouwer", "de Wit", "Dijkstra", "Smits", "de Graaf",
        "van der Meer", "Kok", "Jacobs", "Vermeulen", "Yilmaz", "El Amrani", "Kaya", "Nguyen"]
CITIES = ["Amsterdam", "Rotterdam", "Utrecht", "Den Haag", "Eindhoven", "Groningen",
          "Haarlem", "Leiden", "Arnhem", "Zwolle", "Breda", "Nijmegen"]

PROVIDER_TYPES = {
    "gp": ["Huisartsenpraktijk {c} Centrum", "Huisartsen {c} Zuid", "Gezondheidscentrum {c}"],
    "specialist": ["{c} Medisch Centrum", "Ziekenhuis {c}"],
    "imaging": ["{c} Diagnostisch Centrum", "MRI Kliniek {c}"],
    "pharmacy": ["Apotheek {c}", "Stadsapotheek {c}"],
    "mental_health": ["GGZ Praktijk {c}", "Psychologenpraktijk {c}"],
    "physiotherapy": ["Fysiotherapie {c}", "Fysio & Sport {c}", "Praktijk voor Fysiotherapie {c}"],
    "dental": ["Tandartspraktijk {c}", "Mondzorg {c}"],
    "glasses": ["Opticien {c}", "Brillen & Lenzen {c}"],
    "alternative": ["Acupunctuur {c}", "Osteopathie {c}"],
}

# category -> list of (treatment_code, description, diagnosis_code, diagnosis, price range)
TREATMENTS = {
    "gp": [("GP-01", "GP consultation (regular)", "J06.9", "Upper respiratory infection", (12, 12)),
           ("GP-02", "GP consultation (long)", "I10", "Hypertension check", (24, 24)),
           ("GP-03", "GP home visit", "R50.9", "Fever", (30, 30))],
    "specialist": [("SP-10", "Outpatient consultation orthopaedics", "M17.1", "Knee osteoarthritis", (180, 420)),
                   ("SP-20", "Cardiology work-up", "I48", "Atrial fibrillation", (350, 1200)),
                   ("SP-30", "Day surgery (arthroscopy)", "S83.2", "Meniscus tear", (1800, 3600)),
                   ("SP-40", "Dermatology consultation", "L40", "Psoriasis", (150, 380))],
    "imaging": [("IM-01", "MRI scan knee", "S83.5", "Knee ligament injury", (350, 520)),
                ("IM-02", "MRI scan lumbar spine", "M54.5", "Low back pain", (380, 560)),
                ("IM-03", "CT scan thorax", "R07.4", "Chest pain", (300, 450))],
    "pharmacy": [("PH-01", "Metformin, 3 months", "E11", "Type 2 diabetes", (15, 40)),
                 ("PH-02", "Salbutamol inhaler", "J45", "Asthma", (12, 30)),
                 ("PH-03", "Adalimumab, 1 month", "M05", "Rheumatoid arthritis", (450, 900)),
                 ("PH-04", "Amoxicillin course", "J01", "Sinusitis", (8, 15))],
    "mental_health": [("MH-01", "Basic mental health care, short", "F41.1", "Generalised anxiety", (450, 650)),
                      ("MH-02", "Specialist mental health care", "F32.1", "Moderate depression", (900, 2400))],
    "physiotherapy": [("FY-01", "Physiotherapy session", "M54.5", "Low back pain", (38, 42)),
                      ("FY-01", "Physiotherapy session", "M75.1", "Shoulder impingement", (38, 42)),
                      ("FY-02", "Manual therapy session", "M54.2", "Neck pain", (48, 52)),
                      ("FY-01", "Physiotherapy session", "S83.5", "Knee ligament rehab", (38, 42)),
                      ("FY-03", "Physiotherapy session (chronic list)", "G35", "Multiple sclerosis", (38, 42))],
    "dental": [("DE-01", "Periodic check-up", "Z01.2", "Dental check-up", (25, 30)),
               ("DE-02", "Filling, two surfaces", "K02.1", "Dental caries", (70, 110)),
               ("DE-03", "Root canal treatment", "K04.0", "Pulpitis", (300, 650))],
    "glasses": [("GL-01", "Prescription glasses", "H52.1", "Myopia", (180, 450)),
                ("GL-02", "Contact lenses, 6 months", "H52.1", "Myopia", (90, 160))],
    "alternative": [("AL-01", "Acupuncture session", "M54.5", "Low back pain", (60, 80)),
                    ("AL-02", "Osteopathy session", "M54.2", "Neck pain", (70, 90))],
}

# expected claims per member per year by category (rough)
FREQ = {"gp": 2.5, "specialist": 0.6, "imaging": 0.2, "pharmacy": 3.0, "mental_health": 0.15,
        "physiotherapy": 0.0, "dental": 1.2, "glasses": 0.3, "alternative": 0.3}


def poisson(lam):
    """Small Poisson sampler (stdlib only)."""
    l, k, p = pow(2.718281828, -lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def rand_date(year):
    start = date(year, 1, 1)
    end = min(date(year, 12, 31), TODAY - timedelta(days=2))
    return start + timedelta(days=rng.randint(0, (end - start).days))


def build_policies(path):
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE policies (
        policy_id TEXT PRIMARY KEY,
        product_name TEXT NOT NULL,
        product_type TEXT NOT NULL CHECK (product_type IN ('basic','supplementary','dental')),
        monthly_premium_eur REAL NOT NULL,
        description TEXT
    );
    CREATE TABLE coverage_rules (
        rule_id INTEGER PRIMARY KEY,
        policy_id TEXT NOT NULL REFERENCES policies(policy_id),
        care_category TEXT NOT NULL,
        category_name TEXT NOT NULL,
        covered_pct INTEGER NOT NULL,
        annual_limit_eur REAL,              -- NULL = no money limit
        max_visits_per_year INTEGER,        -- e.g. max physiotherapy sessions per year
        requires_preauth INTEGER NOT NULL,  -- 1 = prior authorisation needed
        deductible_applies INTEGER NOT NULL,-- 1 = counts towards the yearly deductible
        notes TEXT
    );
    CREATE TABLE deductible_years (
        year INTEGER PRIMARY KEY,
        mandatory_deductible_eur REAL NOT NULL,
        notes TEXT
    );
    CREATE TABLE member_policies (
        member_id TEXT NOT NULL,
        year INTEGER NOT NULL,
        basic_policy_id TEXT NOT NULL REFERENCES policies(policy_id),
        supplementary_policy_id TEXT REFERENCES policies(policy_id),
        dental_policy_id TEXT REFERENCES policies(policy_id),
        voluntary_deductible_eur REAL NOT NULL DEFAULT 0,
        chronic_physio_indication INTEGER NOT NULL DEFAULT 0, -- condition on the chronic list
        PRIMARY KEY (member_id, year)
    );
    """)
    db.executemany("INSERT INTO policies VALUES (?,?,?,?,?)", POLICIES)
    for pid, rules in RULES.items():
        for r in rules:
            db.execute("INSERT INTO coverage_rules (policy_id, care_category, category_name, covered_pct,"
                       " annual_limit_eur, max_visits_per_year, requires_preauth, deductible_applies, notes)"
                       " VALUES (?,?,?,?,?,?,?,?,?)", (pid, r[0], CATEGORIES[r[0]], *r[1:]))
    for y, amt in MANDATORY_DEDUCTIBLE.items():
        db.execute("INSERT INTO deductible_years VALUES (?,?,?)",
                   (y, amt, "Mandatory deductible (eigen risico) for adults 18+. GP care, and "
                            "supplementary cover are exempt. Members can add a voluntary deductible up to EUR 500."))
    return db


def rule_lookup():
    return {(pid, r[0]): r for pid, rules in RULES.items() for r in rules}


def main():
    pol = build_policies(DATA_DIR / "policies.db")
    cpath = DATA_DIR / "claims.db"
    cpath.unlink(missing_ok=True)
    cdb = sqlite3.connect(cpath)
    cdb.executescript("""
    CREATE TABLE members (
        member_id TEXT PRIMARY KEY,
        first_name TEXT, last_name TEXT,
        date_of_birth TEXT, city TEXT, email TEXT
    );
    CREATE TABLE providers (
        provider_id TEXT PRIMARY KEY,
        name TEXT, provider_type TEXT, city TEXT,
        contracted INTEGER NOT NULL  -- 1 = contracted by the insurer
    );
    CREATE TABLE claims (
        claim_id TEXT PRIMARY KEY,
        member_id TEXT NOT NULL REFERENCES members(member_id),
        provider_id TEXT NOT NULL REFERENCES providers(provider_id),
        care_category TEXT NOT NULL,
        service_date TEXT NOT NULL,
        submitted_date TEXT NOT NULL,
        processed_date TEXT,
        physio_visits INTEGER NOT NULL DEFAULT 0,  -- number of physiotherapy sessions in this claim
        preauth_reference TEXT,
        status TEXT NOT NULL CHECK (status IN ('paid','partially_paid','denied','pending')),
        claimed_amount_eur REAL NOT NULL,
        reimbursed_amount_eur REAL,       -- paid by insurer (NULL while pending)
        deductible_applied_eur REAL,      -- part charged to the member's deductible
        member_pays_eur REAL,             -- total left for the member to pay
        covered_by TEXT,                  -- policy id(s) that paid
        decision_reason TEXT
    );
    CREATE TABLE claim_lines (
        line_id INTEGER PRIMARY KEY,
        claim_id TEXT NOT NULL REFERENCES claims(claim_id),
        treatment_code TEXT, description TEXT,
        diagnosis_code TEXT, diagnosis TEXT,
        quantity INTEGER, unit_price_eur REAL, amount_eur REAL
    );
    """)

    # providers
    providers = {cat: [] for cat in CATEGORIES}
    n = 1
    for cat, names in PROVIDER_TYPES.items():
        for city in CITIES:
            for tmpl in rng.sample(names, k=min(len(names), 2 if cat in ("gp", "physiotherapy", "pharmacy") else 1)):
                pid = f"PRV{n:04d}"
                n += 1
                contracted = 1 if rng.random() < 0.85 else 0
                cdb.execute("INSERT INTO providers VALUES (?,?,?,?,?)",
                            (pid, tmpl.format(c=city), cat, city, contracted))
                providers[cat].append((pid, city, contracted))

    rules = rule_lookup()
    claim_no = 100001
    stats = {"members": 0, "claims": 0}

    for i in range(1, N_MEMBERS + 1):
        mid = f"M{100000 + i}"
        fn, ln = rng.choice(FIRST), rng.choice(LAST)
        dob = date(rng.randint(1945, 2006), rng.randint(1, 12), rng.randint(1, 28))
        city = rng.choice(CITIES)
        email = f"{fn.lower()}.{ln.lower().replace(' ', '')}@example.com"
        cdb.execute("INSERT INTO members VALUES (?,?,?,?,?,?)", (mid, fn, ln, dob.isoformat(), city, email))
        stats["members"] += 1

        chronic = 1 if rng.random() < 0.08 else 0
        basic = rng.choice(["BAS-N"] * 3 + ["BAS-R"])
        sup = rng.choices([None, "SUP-1", "SUP-2", "SUP-3"], weights=[25, 30, 30, 15])[0]
        dental = "DEN-1" if rng.random() < 0.45 else None
        voluntary = rng.choices([0, 100, 250, 500], weights=[70, 10, 10, 10])[0]
        physio_user = chronic or rng.random() < 0.35

        for year in YEARS:
            # members occasionally change supplementary package between years
            if year == 2026 and rng.random() < 0.15:
                sup = rng.choice([None, "SUP-1", "SUP-2", "SUP-3"])
            pol.execute("INSERT INTO member_policies VALUES (?,?,?,?,?,?,?)",
                        (mid, year, basic, sup, dental, voluntary, chronic))

            # -------- generate the year's events --------
            events = []
            for cat, lam in FREQ.items():
                for _ in range(poisson(lam)):
                    events.append((rand_date(year), cat, None))
            if physio_user and (chronic or rng.random() < 0.7):
                # physio is billed per treatment episode; sessions 1-4 per claim
                total = rng.randint(25, 40) if chronic else rng.randint(4, 26)
                d = rand_date(year)
                d = min(d, date(year, 6, 30)) if total > 15 else d
                tr = rng.choice(TREATMENTS["physiotherapy"][-1:] if chronic else TREATMENTS["physiotherapy"][:-1])
                while total > 0:
                    v = min(total, rng.randint(2, 4))
                    if d >= min(date(year, 12, 31), TODAY - timedelta(days=2)):
                        break
                    events.append((d, "physiotherapy", (v, tr)))
                    total -= v
                    d += timedelta(days=rng.randint(10, 21))
            events.sort(key=lambda e: e[0])

            ded_total = MANDATORY_DEDUCTIBLE[year] + voluntary
            ded_used = 0.0
            physio_used = 0          # sessions covered/claimed this year
            sup_physio_used = 0
            spend = {}               # (policy, category) -> euros reimbursed

            for svc_date, cat, extra in events:
                cid = f"C{claim_no}"
                claim_no += 1
                prov_list = providers[cat]
                local = [p for p in prov_list if p[1] == city] or prov_list
                prov = rng.choice(local)
                submitted = svc_date + timedelta(days=rng.randint(1, 30))
                if submitted > TODAY:
                    submitted = TODAY
                pending = (TODAY - submitted).days < 14 and rng.random() < 0.7

                # build lines
                visits = 0
                if cat == "physiotherapy":
                    visits, tr = extra
                    price = rng.randint(*tr[4]) * 1.0
                    lines = [(tr[0], tr[1], tr[2], tr[3], visits, price, round(price * visits, 2))]
                else:
                    tr = rng.choice(TREATMENTS[cat])
                    price = round(rng.uniform(*tr[4]), 2) if tr[4][0] != tr[4][1] else float(tr[4][0])
                    lines = [(tr[0], tr[1], tr[2], tr[3], 1, price, price)]
                amount = round(sum(l[6] for l in lines), 2)

                preauth = None
                if cat == "imaging":
                    preauth = f"PA-{year}-{rng.randint(10000, 99999)}" if rng.random() < 0.8 else None

                reimb = ded = member = None
                status, reason, covered_by = "pending", "Claim received, awaiting assessment.", None
                processed = None

                if not pending:
                    processed = (submitted + timedelta(days=rng.randint(3, 15)))
                    processed = min(processed, TODAY).isoformat()
                    reimb, ded, member, status, reason, covered_by = 0.0, 0.0, amount, "denied", "", None

                    if cat == "physiotherapy":
                        s_rule = rules.get((sup, cat)) if sup else None
                        sup_left = max(0, s_rule[3] - sup_physio_used) if s_rule else 0
                        by_sup = min(visits, sup_left)
                        sup_physio_used += by_sup
                        rest = visits - by_sup
                        by_basic = 0
                        if chronic and rest:
                            first_basic_visit = max(physio_used + by_sup + 1, CHRONIC_BASIC_FROM_VISIT)
                            last_visit = physio_used + visits
                            by_basic = max(0, min(rest, last_visit - first_basic_visit + 1))
                        physio_used += visits
                        sup_amt = by_sup * price
                        basic_amt = by_basic * price
                        ded = min(basic_amt, ded_total - ded_used)
                        ded_used += ded
                        reimb = round(sup_amt + basic_amt - ded, 2)
                        member = round(amount - reimb, 2)
                        payers = [p for p, n_ in ((sup, by_sup), (basic, by_basic)) if n_]
                        covered_by = ", ".join(payers) or None
                        uncovered = visits - by_sup - by_basic
                        if uncovered == 0:
                            status = "paid"
                            reason = f"{visits} session(s) covered" + (
                                f"; EUR {ded:.2f} charged to deductible." if ded else ".")
                        elif uncovered == visits:
                            status = "denied"
                            if chronic:
                                reason = ("Not covered yet: "
                                          + (f"the {s_rule[3]} sessions under {sup} are used up and " if s_rule
                                             else "no supplementary package with physiotherapy, and ")
                                          + f"basic insurance only covers chronic-list physiotherapy from the "
                                          f"{CHRONIC_BASIC_FROM_VISIT}st session in {year}.")
                            elif not s_rule:
                                reason = ("Physiotherapy is not covered: no supplementary package with "
                                          "physiotherapy, and no chronic indication under basic insurance.")
                            else:
                                reason = (f"Annual physiotherapy limit reached: {s_rule[3]} of {s_rule[3]} "
                                          f"sessions under {sup} already used in {year}.")
                        else:
                            status = "partially_paid"
                            parts = []
                            if by_sup:
                                parts.append(f"{by_sup} under {sup}")
                            if by_basic:
                                parts.append(f"{by_basic} under basic insurance")
                            why = (f"the annual limit of {s_rule[3]} sessions under {sup} is reached" if s_rule
                                   else "there is no supplementary package with physiotherapy")
                            if chronic:
                                why += (f" and basic insurance only covers chronic-list physiotherapy from the "
                                        f"{CHRONIC_BASIC_FROM_VISIT}st session")
                            reason = (f"{visits - uncovered} of {visits} sessions covered ({', '.join(parts)}); "
                                      f"{uncovered} session(s) not covered because {why}.")
                    else:
                        payer = None
                        for p in (basic, sup, dental):
                            if p and (p, cat) in rules:
                                payer = p
                                break
                        if not payer:
                            reason = f"{CATEGORIES[cat]} is not covered by the member's policies in {year}."
                        else:
                            r = rules[(payer, cat)]
                            covered_pct, limit, _, preauth_req, ded_applies = r[1], r[2], r[3], r[4], r[5]
                            if preauth_req and not preauth:
                                reason = "Denied: prior authorisation is required for this treatment and none was found."
                            elif cat == "specialist" and prov[2] == 0 and payer == "BAS-N" and rng.random() < 0.5:
                                eligible = round(amount * 0.75, 2)
                                ded = min(eligible, ded_total - ded_used) if ded_applies else 0.0
                                ded_used += ded
                                reimb = round(eligible - ded, 2)
                                member = round(amount - reimb, 2)
                                status, covered_by = "partially_paid", payer
                                reason = ("Non-contracted provider under an in-kind policy: 75% of the "
                                          "cost reimbursed" + (f"; EUR {ded:.2f} charged to deductible." if ded else "."))
                            else:
                                eligible = round(amount * covered_pct / 100, 2)
                                if limit is not None:
                                    left = max(0.0, limit - spend.get((payer, cat), 0.0))
                                    eligible = min(eligible, left)
                                ded = round(min(eligible, ded_total - ded_used), 2) if ded_applies else 0.0
                                ded_used += ded
                                reimb = round(eligible - ded, 2)
                                spend[(payer, cat)] = spend.get((payer, cat), 0.0) + eligible
                                member = round(amount - reimb, 2)
                                covered_by = payer
                                if eligible == 0 and limit is not None:
                                    status = "denied"
                                    reason = f"Annual limit of EUR {limit:.0f} under {payer} already used."
                                elif member == 0:
                                    status, reason = "paid", "Fully covered."
                                elif ded and eligible == amount:
                                    status = "paid"
                                    reason = f"Covered; EUR {ded:.2f} charged to the yearly deductible."
                                else:
                                    status = "partially_paid"
                                    bits = []
                                    if covered_pct < 100:
                                        bits.append(f"{covered_pct}% reimbursed")
                                    if limit is not None and eligible < amount * covered_pct / 100:
                                        bits.append(f"annual limit of EUR {limit:.0f} reached")
                                    if ded:
                                        bits.append(f"EUR {ded:.2f} charged to deductible")
                                    reason = "Partially covered: " + "; ".join(bits) + "."
                    reimb, ded, member = round(reimb, 2), round(ded, 2), round(member, 2)

                cdb.execute("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (cid, mid, prov[0], cat, svc_date.isoformat(), submitted.isoformat(), processed,
                             visits, preauth, status, amount, reimb, ded, member, covered_by, reason))
                for l in lines:
                    cdb.execute("INSERT INTO claim_lines (claim_id, treatment_code, description, diagnosis_code,"
                                " diagnosis, quantity, unit_price_eur, amount_eur) VALUES (?,?,?,?,?,?,?,?)",
                                (cid, *l))
                stats["claims"] += 1

    cdb.execute("""CREATE VIEW member_physio_visits AS
        SELECT member_id, CAST(substr(service_date,1,4) AS INTEGER) AS year,
               SUM(physio_visits) AS physio_visits_claimed,
               SUM(CASE WHEN status != 'pending' THEN physio_visits ELSE 0 END) AS physio_visits_assessed
        FROM claims WHERE care_category = 'physiotherapy'
        GROUP BY member_id, year""")
    cdb.execute("""CREATE VIEW member_deductible_used AS
        SELECT member_id, CAST(substr(service_date,1,4) AS INTEGER) AS year,
               ROUND(SUM(COALESCE(deductible_applied_eur,0)),2) AS deductible_used_eur
        FROM claims GROUP BY member_id, year""")
    for db in (cdb, pol):
        db.commit()
        db.close()
    print(f"Created {stats['members']} members and {stats['claims']} claims "
          f"in {DATA_DIR / 'claims.db'} and {DATA_DIR / 'policies.db'}")


if __name__ == "__main__":
    main()
