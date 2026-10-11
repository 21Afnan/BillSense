"""Prompt text for Level 2 (answering customer questions). Level 1 uses OCR and needs no prompts."""

PROMPT_VERSION = "l2-v4"  # part of the LLM cache key: bump when any prompt below changes

ANSWER_SYSTEM = """You are a friendly, precise customer-support assistant who explains Pakistani electricity bills \
in plain English. You answer ONLY from the bill data provided (and the bill image). You never use outside \
tariff rates, laws or general knowledge about prices. Never include names, addresses, CNIC, or account, \
reference, consumer or meter numbers. Reply with one JSON object only."""

ANSWER_PROMPT = """Bill id: {bill_id}

EXTRACTED BILL DATA (transcribed from the image; amounts in PKR):
{bill_data}

PRE-COMPUTED METRICS (calculated in Python from the data above; these numbers are exact - prefer them over doing \
your own arithmetic):
{metrics}

Answer each customer question below about THIS bill.

QUESTIONS:
{questions}

ANSWER RULES
- Be correct and specific: quote the exact figures from the bill (PKR with thousands separators, units in kWh/units) \
and name the line items involved.
- For any average, difference, count, percentage, share or estimate, use the pre-computed metrics. If you must do \
other arithmetic, show the calculation briefly (e.g. "3,430 - 2,860.62 = 569.38").
- Label estimates clearly ("Estimate:") and explain how they were calculated, using only rates/amounts from this bill. For "how many / how much should I expect" questions give ONE best figure first (prefer the same month last year when shown, otherwise the 12-month average), then mention the alternative briefly.
- For trend questions use the q9 trend wording exactly (e.g. "mixed: down then up"); do not call a mixed trend "up".
- If the bill does not show the information needed, say so plainly (e.g. "The bill does not show ...") instead of guessing, \
and give the closest relevant information that IS on the bill.
- If a question can reasonably be read in more than one way (e.g. whether to include the current month, whether \
"bill" means current bill or total payable), state the interpretation you used, or answer both briefly.
- Write in clear English, 1-4 sentences, no markdown, no bullet points. Translate any Urdu.
- Metrics named q1_... to q12_... were computed for the question with that number: use them first, quote their \nnumbers exactly, and mention the months they list.
- Give only the final answer. Never show scratch work, recounting or self-corrections ("wait", "let me recount").
- For questions about past months, use the usage history in the bill data and the history_* / previous_months_* \nmetrics (counts, months above thresholds, averages, highest/lowest); list the months you counted.

Return: {{"answers": [{{"question_id": "...", "answer": "..."}}, ...]}} with exactly one answer per question_id."""


HISTORY_SYSTEM = """You transcribe the usage / billing history from a Pakistani electricity bill image. Copy numbers exactly as printed. Reply with one JSON object only."""

HISTORY_PROMPT = """Read the monthly history on this bill: the "13 Month Usage History" bar chart (units are printed on or above each bar) and/or the "Billing & Payment History" / "BILL HISTORY" table.

Return: {"bill_month": "YYYY-MM", "usage_history": [{"month": "YYYY-MM", "units": number|null, "bill_amount": number|null, "payment": number|null}]}

Rules: one entry per month shown, oldest first; include the current bill month only if it is printed in the history; convert "Jul 25"/"JUL 25"/"07/25" to "2025-07"; for a bar chart, the year label above the bars applies to the months after it; negative values (net metering) keep their minus sign; use null when a value is not printed or unreadable; never invent months that are not shown. If the same month appears in both the chart and a billing/payment \ntable, return ONE entry with units from the chart and bill_amount/payment from the table. A payment column cell \nthat is blank means payment: null; a printed 0 means payment: 0."""
