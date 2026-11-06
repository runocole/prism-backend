"""
jobs/questions.py
──────────────────
Custom questions on job / screening links (JobLink.questions):
  - validate_questions()  — check what HR built in the question builder
  - visible_questions()   — which questions apply given the answers ("show if")
  - clean_answers()       — validate a candidate's answers
  - check_preferred()     — screening: do the answers match HR's preferred answers?
  - STANDARD_QUESTIONS    — starter set (the questions the app used to have built in)
"""

import re
import uuid

TYPES = ("choice", "yes_no", "number", "text")
YES_NO = ["Yes", "No"]

NIGERIAN_STATES = [
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue",
    "Borno", "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu",
    "FCT - Abuja", "Gombe", "Imo", "Jigawa", "Kaduna", "Kano", "Katsina",
    "Kebbi", "Kogi", "Kwara", "Lagos", "Nasarawa", "Niger", "Ogun", "Ondo",
    "Osun", "Oyo", "Plateau", "Rivers", "Sokoto", "Taraba", "Yobe", "Zamfara",
]


def _q(id, text, type, options=None, show_if=None):
    return {
        "id": id, "text": text, "type": type, "options": options or [],
        "required": True, "show_if": show_if, "preferred": [], "min": None, "max": None,
    }


# The questions that used to be hard-coded. Ids match the old answer keys so
# answers given before the switch to custom questions still line up.
STANDARD_QUESTIONS = [
    _q("state_of_residence", "State of residence", "choice", NIGERIAN_STATES),
    _q("willing_to_relocate", "Are you willing to relocate to Lagos?", "choice",
       ["Yes", "No", "Open to discussion"],
       {"question": "state_of_residence", "op": "is_not", "value": "Lagos"}),
    _q("age_range", "Age range", "choice", ["18-20", "21-25", "26-30", "31-35", "35+"]),
    _q("nysc_completion", "Have you completed NYSC?", "yes_no"),
    _q("years_of_experience", "Years of relevant experience", "choice",
       ["None", "1-3 years", "3-5 years", "5+ years"]),
    _q("currently_employed", "Are you currently employed?", "yes_no"),
    _q("reason_for_leaving", "Reason for leaving current job", "choice",
       ["Better career opportunity", "Higher compensation", "Relocation", "Company instability",
        "Career change", "Redundancy / Layoff", "Contract ended", "Personal reasons", "Other"],
       {"question": "currently_employed", "op": "is", "value": "Yes"}),
    _q("salary_expectation", "Salary expectation (₦, monthly gross)", "number"),
    _q("available_to_resume", "Available to resume in", "choice",
       ["Immediately", "1 week", "2 weeks", "1 month", "3 months", "More than 3 months"]),
]


def from_legacy(keys: list, preferred: dict) -> list:
    """Turn an old job's fixed question keys + preferred answers into custom questions."""
    by_id = {q["id"]: q for q in STANDARD_QUESTIONS}
    chosen = []
    for key in keys or []:
        if key not in by_id:
            continue
        chosen.append(key)
        if key == "state_of_residence":
            chosen.append("willing_to_relocate")  # the old form added this automatically

    questions = []
    for key in chosen:
        q = dict(by_id[key])
        if q["show_if"] and q["show_if"]["question"] not in chosen:
            q["show_if"] = None
        values = (preferred or {}).get(key) or []
        if key == "salary_expectation":
            q["max"] = _to_number(values[0]) if values else None
        else:
            q["preferred"] = [v for v in values if v in options_of(q)]
        questions.append(q)
    return questions


def options_of(q: dict) -> list:
    return YES_NO if q.get("type") == "yes_no" else list(q.get("options") or [])


def _to_number(value):
    if value is None:
        return None
    digits = re.sub(r"[^\d.]", "", str(value))
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


class QuestionError(ValueError):
    pass


def validate_questions(questions) -> list:
    """Normalise and check questions from the builder. Raises QuestionError."""
    if not isinstance(questions, list):
        raise QuestionError("Questions must be a list.")
    if len(questions) > 50:
        raise QuestionError("A link can have at most 50 questions.")

    cleaned, seen = [], set()
    for i, raw in enumerate(questions, start=1):
        if not isinstance(raw, dict):
            raise QuestionError(f"Question {i} is invalid.")
        text = str(raw.get("text") or "").strip()
        if not text:
            raise QuestionError(f"Question {i} has no text.")
        qtype = raw.get("type")
        if qtype not in TYPES:
            raise QuestionError(f'Question {i} ("{text}") has an unknown type.')
        qid = str(raw.get("id") or "").strip() or f"q_{uuid.uuid4().hex[:8]}"
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", qid) or qid in seen:
            qid = f"q_{uuid.uuid4().hex[:8]}"
        seen.add(qid)

        options = []
        if qtype == "choice":
            options = []
            for o in raw.get("options") or []:
                o = str(o).strip()
                if o and o not in options:
                    options.append(o)
            if len(options) < 2:
                raise QuestionError(f'Question {i} ("{text}") needs at least 2 options.')

        q = {
            "id": qid, "text": text, "type": qtype, "options": options,
            "required": bool(raw.get("required", True)),
            "show_if": None, "preferred": [], "min": None, "max": None,
        }

        show_if = raw.get("show_if")
        if isinstance(show_if, dict) and show_if.get("question"):
            if show_if["question"] not in seen - {qid}:
                raise QuestionError(f'Question {i} ("{text}") can only depend on a question above it.')
            q["show_if"] = {
                "question": show_if["question"],
                "op": "is_not" if show_if.get("op") == "is_not" else "is",
                "value": str(show_if.get("value") or ""),
            }

        if qtype in ("choice", "yes_no"):
            allowed = options_of(q)
            q["preferred"] = [p for p in raw.get("preferred") or [] if p in allowed]
        elif qtype == "number":
            q["min"] = _to_number(raw.get("min"))
            q["max"] = _to_number(raw.get("max"))

        cleaned.append(q)
    return cleaned


def public_questions(questions: list) -> list:
    """What candidates see — never the preferred answers."""
    return [
        {k: q.get(k) for k in ("id", "text", "type", "options", "required", "show_if")}
        | {"options": options_of(q)}
        for q in questions
    ]


def is_visible(q: dict, answers: dict) -> bool:
    cond = q.get("show_if")
    if not cond:
        return True
    given = answers.get(cond["question"])
    if given in (None, ""):
        return False
    return (given == cond["value"]) if cond["op"] == "is" else (given != cond["value"])


def visible_questions(questions: list, answers: dict) -> list:
    return [q for q in questions if is_visible(q, answers)]


def clean_answers(questions: list, answers) -> tuple[dict, list]:
    """Validate a candidate's answers. Returns (clean_answers, errors)."""
    answers = answers if isinstance(answers, dict) else {}
    answers = {k: (str(v).strip() if v is not None else "") for k, v in answers.items()}
    clean, errors = {}, []

    for q in questions:
        if not is_visible(q, clean | answers):
            continue
        value = answers.get(q["id"], "")
        if value == "":
            if q.get("required"):
                errors.append(f'Please answer: "{q["text"]}"')
            continue
        if q["type"] in ("choice", "yes_no") and value not in options_of(q):
            errors.append(f'Please choose one of the options for: "{q["text"]}"')
            continue
        if q["type"] == "number":
            if _to_number(value) is None:
                errors.append(f'Please enter a number for: "{q["text"]}"')
                continue
            value = re.sub(r"[^\d.]", "", value)
        clean[q["id"]] = value[:2000]
    return clean, errors


def check_preferred(questions: list, answers: dict) -> list:
    """Return the questions whose answer doesn't match HR's preferred answer."""
    mismatches = []
    for q in visible_questions(questions, answers):
        value = answers.get(q["id"])
        if value in (None, ""):
            continue
        if q["type"] in ("choice", "yes_no"):
            if q.get("preferred") and value not in q["preferred"]:
                mismatches.append(q["text"])
        elif q["type"] == "number":
            number = _to_number(value)
            if number is None:
                continue
            if q.get("min") is not None and number < q["min"]:
                mismatches.append(f'{q["text"]} (below {q["min"]:,.0f})')
            elif q.get("max") is not None and number > q["max"]:
                mismatches.append(f'{q["text"]} (above {q["max"]:,.0f})')
    return mismatches
