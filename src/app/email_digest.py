"""Renders the daily digest email and sends it via Resend.

The HTML uses inline styles and a single-column table layout, since most
email clients (Gmail, Outlook) strip <style> blocks and don't support modern CSS.
A plain-text version is always sent alongside it.
"""

from __future__ import annotations

from datetime import date
from html import escape

import resend
from pydantic import BaseModel

from app.agent.editor import RankedDigest
from app.config import settings


def greeting(name: str, day: date) -> str:
    """The email's first line, e.g. 'Hey Aaron, this is your daily digest of AI news for November 3, 2026.'"""
    return f"Hey {name}, this is your daily digest of AI news for {format_day(day)}."


def format_day(day: date) -> str:
    return f"{day:%B} {day.day}, {day.year}"


class DigestEmail(BaseModel):
    subject: str
    html: str
    text: str


def render_digest_email(*, name: str, day: date, introduction: str, stories: list[RankedDigest]) -> DigestEmail:
    subject = f"Your AI news digest for {format_day(day)}"
    hello = greeting(name, day)
    return DigestEmail(
        subject=subject,
        html=_render_html(subject=subject, hello=hello, introduction=introduction, stories=stories),
        text=_render_text(hello=hello, introduction=introduction, stories=stories),
    )


def _render_html(*, subject: str, hello: str, introduction: str, stories: list[RankedDigest]) -> str:
    story_rows = "\n".join(
        f"""
        <tr>
          <td style="padding:20px 0;border-top:1px solid #e5e7eb;">
            <div style="font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:0.04em;">
              #{story.rank} &middot; {escape(story.source)}
            </div>
            <a href="{escape(story.url, quote=True)}" style="display:block;margin:6px 0 8px;font-size:18px;line-height:1.35;font-weight:600;color:#111827;text-decoration:none;">
              {escape(story.title)}
            </a>
            <p style="margin:0 0 10px;font-size:15px;line-height:1.55;color:#374151;">{escape(story.summary)}</p>
            <p style="margin:0 0 10px;font-size:13px;line-height:1.5;color:#6b7280;font-style:italic;">
              Why it's here: {escape(story.reasoning)}
            </p>
            <a href="{escape(story.url, quote=True)}" style="font-size:14px;color:#2563eb;text-decoration:none;">Read more &rarr;</a>
          </td>
        </tr>"""
        for story in stories
    )
    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(subject)}</title>
  </head>
  <body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;">
      <tr>
        <td align="center" style="padding:24px 12px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:8px;">
            <tr>
              <td style="padding:32px 28px 12px;">
                <p style="margin:0 0 12px;font-size:17px;line-height:1.5;font-weight:600;color:#111827;">{escape(hello)}</p>
                <p style="margin:0 0 8px;font-size:15px;line-height:1.6;color:#374151;">{escape(introduction)}</p>
              </td>
            </tr>
            <tr>
              <td style="padding:0 28px 12px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{story_rows}
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 28px 28px;font-size:12px;line-height:1.5;color:#9ca3af;border-top:1px solid #e5e7eb;">
                Ranked for you by your AI news aggregator. Edit your interests in src/app/profile.py.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def _render_text(*, hello: str, introduction: str, stories: list[RankedDigest]) -> str:
    sections = [
        f"#{story.rank} [{story.source}] {story.title}\n{story.summary}\nWhy it's here: {story.reasoning}\n{story.url}"
        for story in stories
    ]
    return f"{hello}\n\n{introduction}\n\n" + "\n\n".join(sections) + "\n"


def send_email(email: DigestEmail, *, to: str | None = None) -> str:
    """Send the email via Resend and return Resend's message id.

    Defaults to the configured `EMAIL_TO` recipient. Raises if Resend isn't
    configured, rather than silently skipping the send.
    """
    recipient = to or settings.email_to
    if not settings.resend_api_key:
        raise ValueError("RESEND_API_KEY is not set, so the digest email can't be sent")
    if not recipient:
        raise ValueError("EMAIL_TO is not set, so there's no one to send the digest email to")

    resend.api_key = settings.resend_api_key
    response = resend.Emails.send(
        {
            "from": settings.email_from,
            "to": [recipient],
            "subject": email.subject,
            "html": email.html,
            "text": email.text,
        }
    )
    return response["id"]
