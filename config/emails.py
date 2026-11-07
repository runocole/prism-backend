"""
config/emails.py
─────────────────
One branded design for every candidate email (HTML + plain-text fallback).

  send_branded(to, subject, heading=..., paragraphs=[...], details=[(label, value)],
               button=("Start assessment", url), stage=2, ...)

Built with tables and inline styles because email apps (Gmail, Outlook, phone
mail) ignore most modern CSS. Works on phones (single 600px column that
shrinks). Logo comes from the public website; if a mail app blocks images the
company name still shows as text.
"""

import html
import logging
from email.utils import parseaddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)

NAVY = "#02319A"
NAVY_DEEP = "#021f6b"
INK = "#1f2937"
MUTED = "#6b7280"
LINE = "#e5e7eb"
SOFT = "#f3f6fc"

STAGES = ["Applied", "Assessment", "Interview", "Decision"]


def hr_address() -> str:
    return parseaddr(settings.DEFAULT_FROM_EMAIL)[1] or "hr@oticgs.com"


def _e(text) -> str:
    return html.escape(str(text or ""))


def _progress(stage: int) -> str:
    """Stage tracker: stage is 1-based (1 Applied … 4 Decision)."""
    cells = []
    for i, name in enumerate(STAGES, start=1):
        done, current = i < stage, i == stage
        dot_bg = NAVY if (done or current) else "#ffffff"
        dot_fg = "#ffffff" if (done or current) else MUTED
        border = NAVY if (done or current) else LINE
        mark = "&#10003;" if done else str(i)
        weight = "700" if current else "500"
        color = NAVY if current else (INK if done else MUTED)
        cells.append(
            f'<td align="center" style="padding:0 4px;width:25%;">'
            f'<div style="width:28px;height:28px;line-height:28px;border-radius:14px;margin:0 auto;'
            f'background:{dot_bg};color:{dot_fg};border:2px solid {border};font:700 12px Arial,sans-serif;">{mark}</div>'
            f'<div style="margin-top:6px;font:{weight} 12px Arial,sans-serif;color:{color};">{name}</div></td>'
        )
    return (
        f'<div style="font:600 11px Arial,sans-serif;letter-spacing:1px;text-transform:uppercase;color:{MUTED};'
        f'margin:0 0 10px;">Stage {stage} of {len(STAGES)}</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{"".join(cells)}</tr></table>'
    )


def render(*, heading: str, paragraphs: list[str], eyebrow: str = "Application update",
           details: list[tuple[str, str]] | None = None, details_title: str = "",
           button: tuple[str, str] | None = None, after: list[str] | None = None,
           stage: int | None = None, preheader: str = "") -> tuple[str, str]:
    """Return (html, text)."""
    company = _e(settings.COMPANY_NAME)
    logo = f"{settings.FRONTEND_URL}/favicon.png"
    contact = hr_address()

    paras = "".join(
        f'<p style="margin:0 0 14px;font:15px/1.6 Arial,sans-serif;color:{INK};">{_e(p)}</p>' for p in paragraphs
    )
    after_html = "".join(
        f'<p style="margin:0 0 14px;font:15px/1.6 Arial,sans-serif;color:{INK};">{_e(p)}</p>' for p in (after or [])
    )

    details_html = ""
    if details:
        rows = "".join(
            f'<tr><td style="padding:10px 0;border-top:1px solid {LINE};font:13px Arial,sans-serif;color:{MUTED};'
            f'width:38%;vertical-align:top;">{_e(label)}</td>'
            f'<td style="padding:10px 0;border-top:1px solid {LINE};font:600 15px Arial,sans-serif;color:{INK};'
            f'white-space:pre-line;">{_e(value)}</td></tr>'
            for label, value in details
        )
        title = (f'<div style="font:600 11px Arial,sans-serif;letter-spacing:1px;text-transform:uppercase;'
                 f'color:{NAVY};margin-bottom:4px;">{_e(details_title)}</div>') if details_title else ""
        details_html = (
            f'<div style="background:{SOFT};border:1px solid {LINE};border-radius:10px;padding:16px 20px;margin:6px 0 22px;">'
            f'{title}<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table></div>'
        )

    button_html = ""
    if button:
        label, url = button
        button_html = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:4px 0 24px;"><tr>'
            f'<td style="border-radius:8px;background:{NAVY};">'
            f'<a href="{_e(url)}" style="display:inline-block;padding:13px 28px;font:600 15px Arial,sans-serif;'
            f'color:#ffffff;text-decoration:none;border-radius:8px;">{_e(label)}</a></td></tr></table>'
            f'<p style="margin:-14px 0 22px;font:12px/1.5 Arial,sans-serif;color:{MUTED};">'
            f'If the button doesn\'t work, copy this link into your browser:<br>'
            f'<a href="{_e(url)}" style="color:{NAVY};word-break:break-all;">{_e(url)}</a></p>'
        )

    progress_html = (
        f'<div style="border-top:1px solid {LINE};margin-top:8px;padding-top:20px;">{_progress(stage)}</div>'
        if stage else ""
    )

    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(heading)}</title></head>
<body style="margin:0;padding:0;background:#eef1f7;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{_e(preheader or heading)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef1f7;"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:12px;overflow:hidden;">
  <tr><td style="background:{NAVY};background-image:linear-gradient(135deg,{NAVY},{NAVY_DEEP});padding:22px 28px;">
    <table role="presentation" cellpadding="0" cellspacing="0"><tr>
      <td style="vertical-align:middle;padding-right:12px;"><img src="{logo}" width="36" height="36" alt="" style="display:block;border-radius:8px;background:#ffffff;"></td>
      <td style="vertical-align:middle;">
        <div style="font:700 15px Arial,sans-serif;letter-spacing:1px;color:#ffffff;text-transform:uppercase;">{company}</div>
        <div style="font:11px Arial,sans-serif;letter-spacing:2px;color:#c7d3f2;text-transform:uppercase;">Careers &amp; Recruitment</div>
      </td></tr></table>
  </td></tr>
  <tr><td style="padding:30px 28px 10px;">
    <div style="font:600 11px Arial,sans-serif;letter-spacing:1.5px;text-transform:uppercase;color:{NAVY};margin-bottom:8px;">{_e(eyebrow)}</div>
    <h1 style="margin:0 0 18px;font:700 24px/1.3 Arial,sans-serif;color:{INK};">{_e(heading)}</h1>
    {paras}{details_html}{button_html}{after_html}
    <p style="margin:6px 0 22px;font:15px/1.6 Arial,sans-serif;color:{INK};">Kind regards,<br><strong>{company}</strong><br>Recruitment Team</p>
    {progress_html}
  </td></tr>
  <tr><td style="padding:18px 28px 26px;border-top:1px solid {LINE};">
    <p style="margin:0;font:12px/1.6 Arial,sans-serif;color:{MUTED};">
      {company} · Recruitment Team<br>
      Questions? Reply to this email or write to <a href="mailto:{contact}" style="color:{NAVY};">{contact}</a>
    </p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

    text_lines = [heading, ""] + paragraphs
    if details:
        text_lines += [""] + [f"{label}: {value}" for label, value in details]
    if button:
        text_lines += ["", f"{button[0]}: {button[1]}"]
    text_lines += [""] + (after or []) + ["", "Kind regards,", settings.COMPANY_NAME, "Recruitment Team"]
    return page, "\n".join(text_lines)


def send_branded(to: str, subject: str, **kwargs) -> bool:
    """Render and send. Returns True on success; logs and returns False on failure."""
    page, text = render(**kwargs)
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to], reply_to=[hr_address()])
    message.attach_alternative(page, "text/html")
    try:
        message.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("Email to %s failed: %s", to, subject)
        return False
