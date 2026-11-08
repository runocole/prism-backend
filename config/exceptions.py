"""
config/exceptions.py
─────────────────────
Wraps all DRF errors in a consistent response envelope:
  { "success": false, "error": "...", "details": {...} }

This means the frontend always knows exactly what shape to expect
whether the request succeeded or failed.
"""

from rest_framework.views import exception_handler
from rest_framework.response import Response


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is None:
        return response

    error_detail = response.data

    # Flatten DRF's nested error structure into one clean message
    message = _first_message(error_detail) or "Something went wrong."

    response.data = {
        "success": False,
        "error": message,
        "details": error_detail,
    }

    return response


def _first_message(detail, field=""):
    """First readable message in DRF's nested errors, e.g. 'email: Enter a valid email address.'"""
    if isinstance(detail, dict):
        for key, value in detail.items():
            found = _first_message(value, key if key != "non_field_errors" else field)
            if found:
                return found
        return ""
    if isinstance(detail, list):
        for value in detail:
            found = _first_message(value, field)
            if found:
                return found
        return ""
    text = str(detail)
    if not text:
        return ""
    return f"{field}: {text}" if field and field not in ("detail",) else text
