"""The chatbot: Claude plus the read-only database tools in tools.py."""

import anthropic

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
  combine the member's claims with the policy terms, show the numbers you used, and say which claim ids
  or policy rules they come from.
- Keep answers short and structured: a direct answer first, then the supporting details. Use EUR amounts
  with two decimals. Use Markdown tables only when listing several claims.
- If something can't be answered from the data, say so plainly. This is a prototype: don't give
  medical advice, and mention that final decisions are made by the insurer when it matters.
- The current year is {CURRENT_YEAR}; today is 17 November {CURRENT_YEAR}."""

SYSTEM_MEMBER = """
You are talking to member {member_id} ({name}) about their own insurance. Speak to them as "you".
You can only see this member's own claims and policies; the tools fill in their member id automatically.
Help them understand what care they have used, what was reimbursed and why, and what they can still use."""

SYSTEM_EMPLOYEE = """
You are talking to an employee of the insurer (claims handler / customer service). They may ask about any
member, about policies, or for totals and trends across all data. Use find_members to look up members by
name, and run_sql for aggregate questions. Refer to members by name and member id."""


class ChatSession:
    def __init__(self, role, member_id=None, member_name=None):
        self.role = role
        self.member_id = member_id
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY (or an `ant auth login` profile)
        self.tools = tool_definitions(role)
        extra = (SYSTEM_MEMBER.format(member_id=member_id, name=member_name) if role == "member"
                 else SYSTEM_EMPLOYEE)
        self.system = SYSTEM_BASE + "\n" + extra
        self.messages = []

    def ask(self, question):
        """Send a question, run tool calls until Claude answers. Returns (answer_text, tools_used)."""
        start = len(self.messages)
        try:
            return self._ask(question)
        except Exception:
            # Drop the half-finished turn so a failed request can't leave a tool call
            # without its result in the history (which would break every later question).
            del self.messages[start:]
            raise

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
                tools_used.append({"tool": call.name, "input": call.input, "error": is_error})
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": output, "is_error": is_error})
            self.messages.append({"role": "user", "content": results})
        return "Sorry, that took too many lookups. Could you narrow the question down?", tools_used
