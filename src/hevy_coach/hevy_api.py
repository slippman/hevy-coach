"""Small client for Hevy's public Pro API."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://api.hevyapp.com/v1"


class HevyAPIError(RuntimeError):
    """Raised when Hevy cannot return a usable API response."""


class HevyAPI:
    """Read workout changes from the authenticated Hevy account."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Hevy API key cannot be empty")
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _get(self, path: str, parameters: Mapping[str, object]) -> dict[str, Any]:
        query = urlencode(parameters)
        suffix = f"?{query}" if query else ""
        request = Request(
            f"{self._base_url}{path}{suffix}",
            headers={"api-key": self._api_key, "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                payload = json.load(response)
        except HTTPError as error:
            if error.code in {401, 403}:
                raise HevyAPIError(
                    "Hevy rejected the API key or Pro API access is unavailable."
                ) from error
            raise HevyAPIError(f"Hevy API request failed with HTTP {error.code}.") from error
        except (URLError, TimeoutError) as error:
            reason = getattr(error, "reason", None) or error
            raise HevyAPIError(f"Could not reach the Hevy API: {reason}") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise HevyAPIError("Hevy returned an invalid JSON response.") from error
        if not isinstance(payload, dict):
            raise HevyAPIError("Hevy returned an unexpected response shape.")
        return payload

    def workout_events(self, since: str, *, page: int = 1, page_size: int = 10) -> dict[str, Any]:
        """Return one page of updated or deleted workouts."""
        return self._get(
            "/workouts/events",
            {"since": since, "page": page, "pageSize": page_size},
        )

    def iter_workout_events(self, since: str) -> Iterator[dict[str, Any]]:
        """Yield every workout event since an ISO 8601 timestamp."""
        page = 1
        while True:
            payload = self.workout_events(since, page=page)
            events = payload.get("events")
            page_count = payload.get("page_count")
            if not isinstance(events, list) or not isinstance(page_count, int):
                raise HevyAPIError("Hevy returned malformed workout event data.")
            for event in events:
                if not isinstance(event, dict):
                    raise HevyAPIError("Hevy returned a malformed workout event.")
                yield event
            if page >= page_count:
                return
            page += 1

    def exercise_template(self, template_id: str) -> dict[str, Any]:
        """Return one exercise template, including its progression modality."""
        return self._get(f"/exercise_templates/{quote(template_id, safe='')}", {})
