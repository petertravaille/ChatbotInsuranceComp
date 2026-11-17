# PT Code Chatbot

A working prototype of a chatbot for a Dutch health insurer. Members and employees
ask questions in plain English about health care **claims**, and the bot gives advice
based on the **health policy** terms. All data is synthetic (fake).

## What it can do

- **Members** sign in as themselves and see only their own data: claims and why they
  were paid or denied, how much of the deductible (eigen risico) is used, how many
  physiotherapy visits are used and left, and whether a planned treatment will be covered.
- **Employees** can look up any member, read policy terms, and ask questions across
  all claims (for example "how many physio claims were denied in 2026?").

## How it works

```
data/claims.db    members, providers, claims, claim lines, physio visits per claim
data/policies.db  products, coverage rules (incl. max physio visits per year),
                  deductibles, which member has which policy per year
app/tools.py      read-only lookup tools over both databases
app/chatbot.py    Claude (claude-opus-5-5) calling those tools in a loop
app/server.py     Flask web server; app/static/ holds the chat page and header images
```

Both databases are opened read-only. For members, the server always fills in the
signed-in member's id, so the model cannot read another member's data. Only employees
get the free-form SQL tool, and it accepts a single SELECT only.

### The Dutch insurance rules in the data

| Product | Type | Physio visits / year | Other cover |
|---|---|---|---|
| Basic Care In-Kind (BAS-N) / Free Choice (BAS-R) | basic | chronic list only, from 21st session | GP, hospital, MRI/CT (prior auth), medication, mental health |
| Extra Care 1 (SUP-1) | supplementary | 9 | glasses EUR 100 |
| Extra Care 2 (SUP-2) | supplementary | 18 | glasses EUR 150, alternative 75% up to EUR 300 |
| Extra Care 3 (SUP-3) | supplementary | 27 | glasses EUR 250, alternative 80% up to EUR 500 |
| Dental Plus (DEN-1) | dental | – | 75% up to EUR 500 |

Mandatory deductible EUR 385 per year, plus an optional voluntary deductible of
EUR 100–500. GP care and supplementary cover don't count towards the deductible.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...                  # Windows: set ANTHROPIC_API_KEY=...
python app/server.py
```

Open http://localhost:5000, choose a member or "employee", and start asking.

To regenerate the data (same result every time, seed 42):

```bash
python data/generate_data.py
```

## Example questions

Member: "How many physio visits do I have left this year?", "Why was my last claim
denied?", "I need 6 more physio sessions. Will they be covered?"

Employee: "Which members have used up their physio visits this year?", "Show the
policy terms for Extra Care 2", "Explain the physio claims of member M100015".
