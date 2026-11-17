"""Ask the chatbot the README's example questions, live (needs ANTHROPIC_API_KEY).

Run:  python tests/ask_examples.py
Prints each answer and the tools Claude called, so you can check them against the data.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from chatbot import ChatSession  # noqa: E402

RUNS = [
    ("member", "M100015", "Amira Bakker", [
        "How many physio visits do I have left this year?",
        "How much of my deductible have I used?",
        "Why was my last claim denied?",
        "I need 6 more physio sessions. Will they be covered?",
    ]),
    ("employee", None, None, [
        "Which members have used up their physio visits this year?",
        "How many claims were denied in 2026, by care category?",
        "Show the policy terms for Extra Care 2",
        "Explain the physio claims of member M100015",
    ]),
]

for role, mid, name, questions in RUNS:
    chat = ChatSession(role, mid, name)
    for q in questions:
        answer, tools = chat.ask(q)
        print(f"\n### [{role}] {q}\nTools: {', '.join(t['tool'] for t in tools) or 'none'}\n{answer}")
