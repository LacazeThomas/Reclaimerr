"""Item metadata lookups that work with server API keys."""

from typing import Any
from urllib.parse import quote

from backend.enums import Service


async def get_item_details(
    client: Any, service: Service, item_id: str
) -> dict[str, Any]:
    if service is Service.JELLYFIN:
        # /Items/{id} requires a user, even with an administrative API key.
        payload = await client._make_request(
            "Items",
            params={
                "Ids": item_id,
                "Fields": "Path,MediaSources",
                "EnableUserData": False,
            },
            timeout=10,
        )
        rows = payload.get("Items") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError("Jellyfin item lookup returned no unique item")
        row = rows[0]
        if (
            not isinstance(row, dict)
            or str(row.get("Id", "")).replace("-", "").casefold()
            != item_id.replace("-", "").casefold()
        ):
            raise ValueError("Jellyfin item lookup returned a different item")
    else:
        row = await client._make_request(f"Items/{quote(item_id, safe='')}", timeout=10)
    if not isinstance(row, dict):
        raise ValueError("Invalid media-server item metadata")
    return row
