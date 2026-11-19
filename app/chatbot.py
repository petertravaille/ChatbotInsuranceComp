"""The chatbot: Claude plus the read-only database tools in tools.py."""

import os
import re

import anthropic

from guards import BLOCKED_ANSWER, number_check, privacy_leak
from tools import CURRENT_YEAR, execute_tool, tool_definitions

MODEL = "claude-opus-5-5"
MAX_TOOL_ROUNDS = 12

SYSTEM_BASE = f"""You are the assistant of a Dutch health insurer, answering in clear, friendly English.
The data is synthetic (fake) and modelled on the Dutch system: everyone has basic insurance
(basisverzekering) with a yearly mandatory deductible (eigen risico, EUR 385), optionally a voluntary
deductible on top, and optional supplementary packages (aanvullende verzekering) for things like
physiotherapy, glasses, alternative medicine and dental care.

Key rules:
- GP care is exempt from the deductible. Supplementary packages never use the deductible.
- Physiotherapy for adults is paid from the supplementary package up to its yearly maximum number of
  visits (Extra Care 1: 9, Extra Care 2: 18, Extra Care 3: 27). Basic insurance only pays physiotherapy
  for conditions on the chronic list, from the 21st session in a year.
- MRI/CT scans need prior authorisation.

How to work:
- Always look facts up with the tools; never guess amounts, visit counts or coverage.
- When you give advice (for example "can I still get physio this year?", "what will this cost me?"),
  combine the member's claims with the policy terms, show the numbers you used, and say which visits
  or policy rules they come from.
- Claims have no id that people know. NEVER mention an internal claim id (like C100123). Refer to a claim
  by its visit: the visit date plus type of care and provider, for example "your physiotherapy visit on
  12 Nov 2026 at Fysiotherapie Utrecht" (for employees, also the member's name). If a member had several
  visits on one date, mention all of them.
- When you list claims in a table, use exactly these columns in this order:
  Visit date | Type of care | Provider | Location | Status | Eigen risico | Claimed | Reimbursed | You pay
  (for employees: "Member pays", and add a Member column after Visit date). Eigen risico is the amount of
  that claim charged to the deductible (EUR 0.00 when none; a dash while pending).
- Whenever a question is about physiotherapy (sessions used or left, coverage, costs or a planned
  treatment), call physio_sessions and ALWAYS include the full table of every physio claim that year,
  oldest first, with columns:
  Visit date | Provider | Location | Sessions | Running total | Status | Eigen risico | Claimed | Reimbursed | You pay
  Below it, give total sessions used versus the covered maximum and how many covered sessions are left.
- Keep answers short and structured: a direct answer first, then the supporting details. Use EUR amounts
  with two decimals. Use Markdown tables only when listing several claims.
- The data covers only the years 2025 and 2026, and only the products in the policy database. If something
  can't be answered from the data (another year, a product or member that doesn't exist), say so plainly
  and never invent claims, amounts or cover.
- If something can't be answered from the data, say so plainly. This is a prototype: don't give
  medical advice, and mention that final decisions are made by the insurer when it matters.
- The current year is {CURRENT_YEAR}; today is 17 November {CURRENT_YEAR}."""

SYSTEM_MEMBER = """
You are talking to member {member_id} ({name}) about their own insurance. Speak to them as "you".
You can only see this member's own claims and policies; the tools fill in their member id automatically.
If they ask about another person's claims or insurance, explain that for privacy you can only discuss
their own data, and give no details about the other person.
Help them understand what care they have used, what was reimbursed and why, and what they can still use."""

SYSTEM_EMPLOYEE = """
You are talking to an employee of the insurer (claims handler / customer service). They may ask about any
member, about policies, or for totals and trends across all data. Use find_members to look up members by
name, physio_usage for physiotherapy limits across members, and run_sql for other totals (but never show claim ids from its results). Refer to members by name and member id."""


CLAIM_ID = re.compile(r"\bC\d{6}\b")


class ChatSession:
    def __init__(self, role, member_id=None, member_name=None):
        self.role = role
        self.member_id = member_id
        # Reads ANTHROPIC_API_KEY. CHATBOT_ANTHROPIC_API_KEY is a fallback for hosted environments (such as
        # Claude Code cloud sessions) that keep ANTHROPIC_API_KEY for themselves.
        alt = os.environ.get("CHATBOT_ANTHROPIC_API_KEY")
        self.client = (anthropic.Anthropic(api_key=alt) if alt and not os.environ.get("ANTHROPIC_API_KEY")
                       else anthropic.Anthropic())
        self.tools = tool_definitions(role)
        extra = (SYSTEM_MEMBER.format(member_id=member_id, name=member_name) if role == "member"
                 else SYSTEM_EMPLOYEE)
        self.system = SYSTEM_BASE + "\n" + extra
        self.messages = []
        self.last_checks = {}   # privacy guard and number check for the latest answer
        self._tool_outputs = []

    def ask(self, question):
        """Send a question, run tool calls until Claude answers. Returns (answer_text, tools_used)."""
        start = len(self.messages)
        self._tool_outputs = []
        try:
            answer, tools_used = self._ask(question)
            if CLAIM_ID.search(answer):
                # Safety net for Pete's rule: internal claim ids must never reach the user.
                answer, more = self._ask("(System check: your last answer showed internal claim ids such as "
                                         f"{CLAIM_ID.search(answer).group()}. Give the same answer again without "
                                         "any claim ids, naming each claim by its visit date, type of care and "
                                         "provider instead.)")
                tools_used += more
                answer = CLAIM_ID.sub("[claim]", answer)
            return self._check(answer, start), tools_used
        except Exception:
            # Drop the half-finished turn so a failed request can't leave a tool call
            # without its result in the history (which would break every later question).
            del self.messages[start:]
            raise

    def _check(self, answer, start):
        """Privacy guard (members) and number check, run on every answer before it is shown."""
        self.last_checks = {"privacy_blocked": None, "numbers": None}
        if self.role == "member":
            leak = privacy_leak(answer, self.member_id)
            if leak:
                self.last_checks["privacy_blocked"] = leak
                # Keep the blocked text out of the history too, so it can't come back in a later answer.
                del self.messages[start + 1:]
                self.messages.append({"role": "assistant", "content": BLOCKED_ANSWER})
                return BLOCKED_ANSWER
        self.last_checks["numbers"] = number_check(answer, self._tool_outputs)
        return answer

    def _ask(self, question):
        self.messages.append({"role": "user", "content": question})
        tools_used = []
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=self.system,
                tools=self.tools,
                messages=self.messages,
                output_config={"effort": "medium"},
                cache_control={"type": "ephemeral"},
                # If the model declines on a safety policy, the API retries on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            # Keep the full content (including thinking blocks) unchanged in the history.
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                return "Sorry, I can't help with that request.", tools_used
            if response.stop_reason == "pause_turn":
                continue
            calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not calls:
                text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "max_tokens":
                    text += "\n\n_(Answer was cut off because it got too long.)_"
                return text or "I couldn't find an answer.", tools_used

            results = []
            for call in calls:
                output, is_error = execute_tool(call.name, call.input, self.role, self.member_id)
                if not is_error:
                    self._tool_outputs.append(output)
                tools_used.append({"tool": call.name, "input": call.input, "error": is_error})
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": output, "is_error": is_error})
            self.messages.append({"role": "user", "content": results})
        return "Sorry, that took too many lookups. Could you narrow the question down?", tools_used
