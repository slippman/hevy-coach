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
        self.event_stream_includes_deletions = True

    def _request(
        self,
        method: str,
        path: str,
        parameters: Mapping[str, object] | None = None,
        body: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        parameters = parameters or {}
        query = urlencode(parameters)
        suffix = f"?{query}" if query else ""
        headers = {"api-key": self._api_key, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self._base_url}{path}{suffix}",
            data=data,
            headers=headers,
            method=method,
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

    def _get(self, path: str, parameters: Mapping[str, object]) -> dict[str, Any]:
        return self._request("GET", path, parameters)

    def workout_events(self, since: str, *, page: int = 1, page_size: int = 10) -> dict[str, Any]:
        """Return one page of updated or deleted workouts."""
        return self._get(
            "/workouts/events",
            {"since": since, "page": page, "pageSize": page_size},
        )

    def iter_workout_events(self, since: str) -> Iterator[dict[str, Any]]:
        """Yield every workout event since an ISO 8601 timestamp."""
        self.event_stream_includes_deletions = True
        page = 1
        while True:
            payload = self.workout_events(since, page=page)
            events = payload.get("events")
            page_count = payload.get("page_count")
            if events is None and isinstance(payload.get("workouts"), list):
                self.event_stream_includes_deletions = False
                events = []
                for item in payload["workouts"]:
                    if not isinstance(item, dict):
                        raise HevyAPIError("Hevy returned a malformed workout.")
                    if item.get("type") in {"updated", "deleted"}:
                        events.append(item)
                    else:
                        events.append({"type": "updated", "workout": item})
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

    def iter_routines(self) -> Iterator[dict[str, Any]]:
        """Yield every saved Hevy routine."""
        page = 1
        while True:
            payload = self._get("/routines", {"page": page, "pageSize": 10})
            routines = payload.get("routines")
            page_count = payload.get("page_count")
            if not isinstance(routines, list) or not isinstance(page_count, int):
                raise HevyAPIError("Hevy returned malformed routine data.")
            for routine in routines:
                if not isinstance(routine, dict):
                    raise HevyAPIError("Hevy returned a malformed routine.")
                yield routine
            if page >= page_count:
                return
            page += 1

    def routine(self, routine_id: str) -> dict[str, Any]:
        """Return one saved Hevy routine."""
        payload = self._get(f"/routines/{quote(routine_id, safe='')}", {})
        routine = payload.get("routine", payload)
        if not isinstance(routine, dict):
            raise HevyAPIError("Hevy returned malformed routine data.")
        return routine

    def update_routine(self, routine_id: str, routine: Mapping[str, object]) -> dict[str, Any]:
        """Replace one saved Hevy routine after the caller has obtained approval."""
        return self._request(
            "PUT",
            f"/routines/{quote(routine_id, safe='')}",
            body={"routine": routine},
        )
