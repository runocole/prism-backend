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
    if isinstance(error_detail, dict):
        first_key = next(iter(error_detail))
        first_val = error_detail[first_key]
        message = first_val[0] if isinstance(first_val, list) else str(first_val)
    elif isinstance(error_detail, list):
        message = str(error_detail[0])
    else:
        message = str(error_detail)

    response.data = {
        "success": False,
        "error": message,
        "details": error_detail,
    }

    return response