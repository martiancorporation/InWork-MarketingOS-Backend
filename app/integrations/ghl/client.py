"""GoHighLevel (LeadConnector) Contacts API — tag-scoped, OR-grouped search
across a single shared location.

InWork's GHL setup for this engagement uses ONE shared location for every
client in scope; each client is distinguished purely by contact/opportunity
tags (see ``Integration.ghl_tags``), not by a separate location id. Every
caller of this client must check tag membership explicitly (``contact_has_tag``)
rather than assume a returned contact carries only the tag it was searched
for — GHL returns whatever tags a contact actually has, and a contact can
legitimately carry more than one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError, ProviderAuthError

_TIMEOUT = 20.0
_MAX_PAGES = 20  # defensive cap, mirrors MetaClient._MAX_PAGES


@dataclass
class GhlContactsPage:
    contacts: list[dict[str, Any]] = field(default_factory=list)
    next_search_after: list[Any] | None = None


class GhlClient:
    def __init__(self, settings=None) -> None:
        self._s = settings or get_settings().integrations

    async def search_contacts(
        self,
        access_token: str,
        location_id: str,
        tags: list[str],
        *,
        page_limit: int = 100,
        search_after: list[Any] | None = None,
    ) -> GhlContactsPage:
        """One page of contacts carrying any of ``tags`` (an OR group — one
        call covers every client tag at once instead of one request per tag).
        Pass ``search_after`` from the previous page's ``next_search_after``
        to continue past the first ``page_limit`` results.
        """
        if not tags:
            raise AppError(
                "At least one tag is required to search GHL contacts.", code="ghl_no_tags"
            )
        body: dict[str, Any] = {
            "locationId": location_id,
            "pageLimit": page_limit,
            "filters": [
                {
                    "group": "OR",
                    "filters": [
                        {"field": "tags", "operator": "eq", "value": tag} for tag in tags
                    ],
                }
            ],
        }
        if search_after:
            body["searchAfter"] = search_after
        payload = await self._post("/contacts/search", access_token, body)
        contacts = payload.get("contacts") or []
        next_cursor = contacts[-1].get("searchAfter") if contacts else None
        return GhlContactsPage(contacts=contacts, next_search_after=next_cursor)

    async def search_all_contacts(
        self,
        access_token: str,
        location_id: str,
        tags: list[str],
        *,
        page_limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every matching contact across as many pages as it takes (capped at
        ``_MAX_PAGES``), for callers that don't need to stream page-by-page."""
        all_contacts: list[dict[str, Any]] = []
        cursor: list[Any] | None = None
        for _ in range(_MAX_PAGES):
            page = await self.search_contacts(
                access_token, location_id, tags, page_limit=page_limit, search_after=cursor
            )
            all_contacts.extend(page.contacts)
            if not page.next_search_after or len(page.contacts) < page_limit:
                break
            cursor = page.next_search_after
        return all_contacts

    async def _post(self, path: str, access_token: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._s.ghl_base_url}{path}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Version": self._s.ghl_api_version,
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                resp = await http.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise AppError(
                f"Could not reach GHL: {exc}", code="ghl_unreachable", status_code=502
            ) from exc
        payload = _safe_json(resp)
        if resp.status_code in (401, 403):
            message = payload.get("message") or resp.text[:200]
            raise ProviderAuthError(f"GHL rejected our credentials: {message}")
        if resp.status_code >= 400:
            message = payload.get("message") or resp.text[:200]
            raise AppError(f"GHL request failed: {message}", code="ghl_api_error", status_code=400)
        return payload


def contact_has_tag(contact: dict[str, Any], tag: str) -> bool:
    """True if ``tag`` is among the contact's tags — a contact can legitimately
    carry more than one, so never assume it's the only one present."""
    return tag in (contact.get("tags") or [])


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {}
