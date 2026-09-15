"""Thin async wrapper around the Brevo transactional email API.

Used exclusively by the daily report email (``app/services/report_email``).
Mirrors ``app/integrations/llm/openrouter.py``'s shape: an ``is_configured``
gate, a lazy httpx client, settings read from ``app/core/config`` (never
hardcoded). This is a background-job integration, not an HTTP-request one, so
failures raise ``BrevoSendError`` (with a ``retryable`` flag) rather than an
``AppError`` — the caller decides whether to retry, not a request handler.
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings

_API_URL = "https://api.brevo.com/v3/smtp/email"
_TIMEOUT_SECONDS = 15.0


class BrevoSendError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class BrevoClient:
    def __init__(self) -> None:
        self._settings = get_settings().brevo

    @property
    def is_configured(self) -> bool:
        return self._settings.is_configured

    async def send_transactional_email(
        self,
        *,
        to: list[dict[str, str]],
        subject: str,
        html_content: str,
    ) -> str:
        """Send one HTML email via Brevo. Returns Brevo's ``messageId``.

        ``to`` is a list of ``{"email": ..., "name": ...}``. Raises
        ``BrevoSendError`` on any failure — ``retryable=True`` for
        timeouts/connection errors/5xx (worth retrying), ``False`` for 4xx
        (bad key, malformed payload — retrying won't help).
        """
        if not self.is_configured:
            raise BrevoSendError("Brevo is not configured.", retryable=False)

        payload = {
            "sender": {"email": self._settings.sender_email, "name": self._settings.sender_name},
            "to": to,
            "subject": subject,
            "htmlContent": html_content,
        }
        headers = {"api-key": self._settings.api_key, "content-type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(_API_URL, json=payload, headers=headers)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            raise BrevoSendError(f"Brevo request failed: {exc}", retryable=True) from exc

        if response.status_code >= 500:
            raise BrevoSendError(
                f"Brevo returned {response.status_code}: {response.text[:300]}", retryable=True
            )
        if response.status_code >= 400:
            raise BrevoSendError(
                f"Brevo returned {response.status_code}: {response.text[:300]}", retryable=False
            )

        data = response.json()
        return str(data.get("messageId", ""))
