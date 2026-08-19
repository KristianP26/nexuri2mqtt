"""Client for the (undocumented) Nexuri smart-flat backend.

Everything here was derived from the official web app's own traffic. Two things
about this API are unusual enough to shape the whole module:

* Errors come back with HTTP 200. A failed read is signalled by ``state ==
  "error"`` in the body, never by the status code.
* A read that names an unknown topic fails *as a whole*, but the response names
  the offending topic in ``rsn``. That makes topic sets discoverable by probing.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

import requests

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://backend.nexuri.cz"

# Fields the login response might carry the token in. The web app stores it as
# "accessToken"; the others are cheap insurance against naming drift.
_TOKEN_FIELDS = ("accessToken", "access_token", "token", "jwt", "auth_token")

# get_list refuses anything above this.
_MAX_PAGE_SIZE = 50


class NexuriError(RuntimeError):
    """API returned an error envelope or an unusable response."""


class NexuriAuthError(NexuriError):
    """Credentials rejected, or the session expired and could not be renewed."""


def _find_token(payload: Any) -> str | None:
    """Recursively hunt for a token field in a decoded JSON body."""
    if isinstance(payload, dict):
        for field in _TOKEN_FIELDS:
            value = payload.get(field)
            if isinstance(value, str) and value:
                return value
        for value in payload.values():
            found = _find_token(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_token(item)
            if found:
                return found
    return None


class NexuriClient:
    def __init__(
        self,
        email: str,
        password: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 20,
    ) -> None:
        self._email = email
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._token: str | None = None
        self._session = requests.Session()
        self._session.headers.update(
            {"Content-Type": "application/json", "Accept": "application/json"}
        )

    # ---------------------------------------------------------------- auth

    def login(self) -> None:
        response = self._session.put(
            f"{self._base_url}/login",
            json={"email": self._email, "password": self._password},
            timeout=self._timeout,
        )
        if response.status_code in (401, 403):
            raise NexuriAuthError("Nexuri rejected the credentials")
        response.raise_for_status()

        try:
            payload = response.json()
        except ValueError as exc:
            raise NexuriAuthError("login response was not JSON") from exc

        token = _find_token(payload)
        if not token:
            raise NexuriAuthError(
                "no token field in the login response; the API shape may have "
                f"changed (top-level keys: {sorted(payload)[:10]})"
            )
        self._token = token
        log.info("authenticated as %s", self._email)

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        """Call the API, logging in once and retrying if the session is stale."""
        if self._token is None:
            self.login()

        for attempt in (1, 2):
            response = self._session.request(
                method,
                f"{self._base_url}{path}",
                json=body,
                headers={"X-Auth-Token": self._token or ""},
                timeout=self._timeout,
            )
            if response.status_code in (401, 403) and attempt == 1:
                log.info("session rejected (%s), re-authenticating", response.status_code)
                self.login()
                continue
            response.raise_for_status()
            try:
                return response.json()
            except ValueError as exc:
                raise NexuriError(f"{path} returned non-JSON body") from exc

        raise NexuriAuthError(f"{path} kept rejecting the session")

    # ----------------------------------------------------------- discovery

    def iter_widgets(self) -> Iterable[dict]:
        """Yield every widget this account can see, walking all pages."""
        page = 1
        while True:
            payload = self._request(
                "PUT",
                "/component_widget/get_list",
                {
                    "page_number": page,
                    "count_on_page": _MAX_PAGE_SIZE,
                    "order_by": "CUSTOMER_ALIAS",
                    "order_schema": "ASC",
                    # The app sends every filter explicitly as null. Mirror it:
                    # omitting them has not been tested against the backend.
                    "system_widget_building_id": None,
                    "building_id": None,
                    "hardware_id": None,
                    "person_id": None,
                    "calendar_action_id": None,
                    "parking_spot_id": None,
                    "garage_id": None,
                    "bicycle_lock_id": None,
                    "bicycle_cluster_id": None,
                    "mailbox_cluster_id": None,
                    "mailbox_id": None,
                    "tenant_id": None,
                    "contract_id": None,
                    "preselected_ids": None,
                },
            )
            content = payload.get("content") or []
            yield from content

            pages = payload.get("pages") or 1
            if page >= pages or not content:
                return
            page += 1

    # ----------------------------------------------------------- readings

    def read(self, widget_id: str, component_id: str, topics: list[str]) -> dict:
        """Read topics for one component.

        Returns a mapping of topic -> raw integer value. Raises
        :class:`UnknownTopic` when the device rejects one of the topics, so the
        caller can drop it and retry.
        """
        if not topics:
            return {}

        payload = self._request(
            "PUT",
            "/measured_data/get_last",
            {
                "widget_id": widget_id,
                "component_id": component_id,
                "topics": topics,
            },
        )

        # Error envelope. HTTP was 200; the body is where the truth lives.
        if payload.get("state") == "error":
            rejected = payload.get("rsn")
            if isinstance(rejected, str) and rejected in topics:
                raise UnknownTopic(rejected)
            raise NexuriError(
                f"read failed for {component_id}: rc={payload.get('rc')} "
                f"rsn={rejected!r}"
            )

        if not payload.get("ok", True):
            raise NexuriError(f"read for {component_id} reported not-ok")

        values: dict[str, int] = {}
        for topic in topics:
            entry = payload.get(topic)
            if isinstance(entry, dict) and "v" in entry:
                values[topic] = entry["v"]
        return values

    def read_probing(
        self, widget_id: str, component_id: str, topics: list[str]
    ) -> tuple[dict, list[str]]:
        """Read, dropping topics the device rejects.

        Returns ``(values, accepted_topics)``. Callers should cache the accepted
        list: every request here reaches real hardware in the flat, so the probe
        is meant to run once per component, not once per poll.
        """
        candidates = list(topics)
        # Bounded so a misbehaving device cannot spin us against the hardware.
        for _ in range(len(topics)):
            if not candidates:
                return {}, []
            try:
                return self.read(widget_id, component_id, candidates), candidates
            except UnknownTopic as exc:
                log.debug("%s rejected topic %s, dropping", component_id, exc.topic)
                candidates = [t for t in candidates if t != exc.topic]
        return {}, []


class UnknownTopic(NexuriError):
    """The device does not know one of the requested topics."""

    def __init__(self, topic: str) -> None:
        super().__init__(f"unknown topic: {topic}")
        self.topic = topic
