from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from ..scheduler.models import FixedEvent, FlexibleEvent
from ..scheduler.optimizer import ScheduledBlock, schedule_events

logger = logging.getLogger(__name__)


class AppleCalendarError(Exception):
    """Base error for Apple Calendar integration issues."""


class AuthorizationError(AppleCalendarError):
    """Raised when OAuth authorization fails."""


class CalendarSyncError(AppleCalendarError):
    """Raised when synchronization fails due to scheduling issues."""


@dataclass(slots=True)
class OAuthToken:
    """Represents an OAuth 2.0 access token."""

    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    token_type: str = "Bearer"
    scope: str | None = None

    def is_expired(self, *, now: datetime | None = None) -> bool:
        reference = now or datetime.now(timezone.utc)
        if self.expires_at is None:
            return False
        return reference >= self.expires_at

    def authorization_header(self) -> str:
        token_type = self.token_type or "Bearer"
        return f"{token_type} {self.access_token}".strip()


@dataclass(slots=True)
class AppleCalendarConfig:
    """Configuration required to interact with the Apple Calendar API."""

    client_id: str
    client_secret: str
    redirect_uri: str
    auth_endpoint: str
    token_endpoint: str
    calendar_url: str


@dataclass(slots=True)
class CalendarEvent:
    """Represents a calendar event in a normalized structure."""

    uid: str
    summary: str
    start: datetime
    end: datetime
    is_flexible: bool = False
    chunk_index: int | None = None
    chunk_count: int | None = None

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def to_ics(self) -> str:
        start = _format_datetime(self.start)
        end = _format_datetime(self.end)
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//calendarv2//EN",
            "BEGIN:VEVENT",
            f"UID:{self.uid}",
            f"SUMMARY:{self.summary}",
            f"DTSTART:{start}",
            f"DTEND:{end}",
        ]
        if self.chunk_index is not None and self.chunk_count is not None:
            lines.append(f"X-CHUNK-INDEX:{self.chunk_index}")
            lines.append(f"X-CHUNK-COUNT:{self.chunk_count}")
        lines.extend(["END:VEVENT", "END:VCALENDAR", ""])
        return "\r\n".join(lines)


TokenFetcher = Callable[[AppleCalendarConfig, dict[str, Any]], dict[str, Any]]
CalDavFactory = Callable[[str, OAuthToken | None], Any]


class AppleCalendarClient:
    """Client responsible for talking to the Apple Calendar CalDAV endpoint."""

    def __init__(
        self,
        config: AppleCalendarConfig,
        *,
        caldav_client_factory: CalDavFactory | None = None,
        token_fetcher: TokenFetcher | None = None,
        logger_instance: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self._token: OAuthToken | None = None
        self._calendar: Any | None = None
        self._caldav_client_factory = caldav_client_factory or _default_caldav_factory
        self._token_fetcher = token_fetcher or _default_token_fetcher
        self._logger = logger_instance or logger

    # Authorization -----------------------------------------------------------------
    def authorize(self, authorization_code: str) -> OAuthToken:
        payload = {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": self.config.redirect_uri,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }
        try:
            token_payload = self._token_fetcher(self.config, payload)
        except Exception as exc:  # pragma: no cover - network failure path
            self._logger.exception("Token exchange failed: %s", exc)
            raise AuthorizationError("Failed to exchange authorization code") from exc

        token = _parse_token_response(token_payload)
        self._token = token
        self._calendar = None
        self._logger.debug("Obtained access token expiring at %s", token.expires_at)
        return token

    def refresh_access_token(self) -> OAuthToken:
        if not self._token or not self._token.refresh_token:
            raise AuthorizationError("No refresh token available")

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self._token.refresh_token,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }
        try:
            token_payload = self._token_fetcher(self.config, payload)
        except Exception as exc:  # pragma: no cover - network failure path
            self._logger.exception("Token refresh failed: %s", exc)
            raise AuthorizationError("Failed to refresh access token") from exc

        token = _parse_token_response(token_payload)
        self._token = token
        self._calendar = None
        self._logger.debug("Refreshed access token expiring at %s", token.expires_at)
        return token

    # Calendar operations -----------------------------------------------------------
    def load_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        calendar = self._get_calendar()
        try:
            raw_events = calendar.date_search(start, end)
        except Exception as exc:  # pragma: no cover - caldav failure path
            self._logger.exception("Failed to load events: %s", exc)
            raise AppleCalendarError("Failed to load events from calendar") from exc

        events: list[CalendarEvent] = []
        for raw in raw_events:
            try:
                events.append(_extract_calendar_event(raw))
            except Exception as exc:
                self._logger.warning("Unable to parse event %s: %s", getattr(raw, "data", raw), exc)
        self._logger.info("Loaded %s events from Apple Calendar", len(events))
        return events

    def create_or_update_event(self, event: CalendarEvent) -> None:
        calendar = self._get_calendar()
        ics = event.to_ics()
        try:
            existing = None
            if hasattr(calendar, "event_by_uid"):
                try:
                    existing = calendar.event_by_uid(event.uid)
                except Exception:
                    existing = None
            if existing:
                if hasattr(existing, "data"):
                    existing.data = ics
                if hasattr(existing, "save"):
                    existing.save()
                elif hasattr(calendar, "save_event"):
                    calendar.save_event(existing)
                else:  # pragma: no cover - defensive path
                    raise AppleCalendarError("Calendar object cannot persist events")
            else:
                if hasattr(calendar, "save_event"):
                    calendar.save_event(ics)
                elif hasattr(calendar, "add_event"):
                    calendar.add_event(ics)
                else:  # pragma: no cover - defensive path
                    raise AppleCalendarError("Calendar object cannot add events")
        except AppleCalendarError:
            raise
        except Exception as exc:  # pragma: no cover - caldav failure path
            self._logger.exception("Failed to persist event %s: %s", event.uid, exc)
            raise AppleCalendarError("Failed to persist event") from exc

        self._logger.info(
            "Synced event %s (%s - %s)",
            event.uid,
            event.start.isoformat(),
            event.end.isoformat(),
        )

    # Internal helpers -------------------------------------------------------------
    def _get_calendar(self) -> Any:
        if self._token and self._token.is_expired():
            self.refresh_access_token()

        if self._calendar is None:
            client = self._caldav_client_factory(self.config.calendar_url, self._token)
            principal = client.principal()
            calendars = principal.calendars()
            if not calendars:
                raise AppleCalendarError("No calendars available for the authenticated user")
            self._calendar = calendars[0]
        return self._calendar


class AppleCalendarSyncService:
    """Service that synchronizes flexible tasks with the Apple Calendar."""

    def __init__(self, client: AppleCalendarClient, *, logger_instance: logging.Logger | None = None) -> None:
        self._client = client
        self._logger = logger_instance or logger

    def sync(
        self,
        *,
        start: datetime,
        end: datetime,
        flexible_events: Iterable[FlexibleEvent],
    ) -> list[CalendarEvent]:
        try:
            external_events = self._client.load_events(start, end)
        except AppleCalendarError as exc:
            self._logger.exception("Unable to load external events: %s", exc)
            raise

        existing_blocks: list[ScheduledBlock] = []
        for event in external_events:
            fixed = FixedEvent(
                event_id=event.uid,
                duration=event.duration,
                start=event.start,
            )
            existing_blocks.append(ScheduledBlock(event.start, event.end, fixed))

        try:
            scheduled = schedule_events(
                existing_blocks,
                list(flexible_events),
                start,
                end,
            )
        except ValueError as exc:
            self._logger.error("Scheduling collision detected: %s", exc)
            raise CalendarSyncError("Unable to schedule flexible events") from exc

        existing_events = {block.event for block in existing_blocks}
        new_blocks = [block for block in scheduled if block.event not in existing_events]

        synced_events: list[CalendarEvent] = []
        for block in new_blocks:
            if not isinstance(block.event, FlexibleEvent):
                continue
            summary = getattr(block.event, "summary", None) or getattr(block.event, "title", None) or block.event.event_id
            calendar_event = CalendarEvent(
                uid=block.event.event_id,
                summary=summary,
                start=block.start,
                end=block.end,
                is_flexible=True,
                chunk_index=block.chunk_index,
                chunk_count=block.chunk_count,
            )
            try:
                self._client.create_or_update_event(calendar_event)
            except AppleCalendarError as exc:
                self._logger.exception("Failed to sync flexible event %s: %s", block.event.event_id, exc)
                continue
            self._logger.debug("Flexible event %s scheduled", block.event.event_id)
            synced_events.append(calendar_event)

        return synced_events


def _parse_token_response(payload: dict[str, Any]) -> OAuthToken:
    access_token = payload.get("access_token")
    if not access_token:
        raise AuthorizationError("Token response did not contain an access token")
    refresh_token = payload.get("refresh_token")
    token_type = payload.get("token_type", "Bearer")
    expires_at: datetime | None = None
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, (int, float)):
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))
    scope = payload.get("scope")
    return OAuthToken(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        token_type=token_type,
        scope=scope,
    )


def _extract_calendar_event(raw_event: Any) -> CalendarEvent:
    if isinstance(raw_event, CalendarEvent):
        return raw_event
    if hasattr(raw_event, "to_calendar_event"):
        return raw_event.to_calendar_event()
    ics_data = getattr(raw_event, "data", None)
    if isinstance(ics_data, bytes):
        ics_data = ics_data.decode("utf-8")
    if isinstance(ics_data, str):
        return _parse_ics(ics_data)
    raise AppleCalendarError("Unsupported event representation returned by CalDAV client")


def _parse_ics(data: str) -> CalendarEvent:
    fields: dict[str, str] = {}
    for line in data.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.split(";", 1)[0]
        if key in {"UID", "SUMMARY", "DTSTART", "DTEND", "X-CHUNK-INDEX", "X-CHUNK-COUNT"}:
            fields[key] = value.strip()
    uid = fields.get("UID")
    summary = fields.get("SUMMARY", "")
    dtstart = fields.get("DTSTART")
    dtend = fields.get("DTEND")
    if not uid or not dtstart or not dtend:
        raise AppleCalendarError("ICS data missing mandatory fields")
    chunk_index = fields.get("X-CHUNK-INDEX")
    chunk_count = fields.get("X-CHUNK-COUNT")
    return CalendarEvent(
        uid=uid,
        summary=summary,
        start=_parse_datetime(dtstart),
        end=_parse_datetime(dtend),
        is_flexible=chunk_index is not None or chunk_count is not None,
        chunk_index=int(chunk_index) if chunk_index is not None else None,
        chunk_count=int(chunk_count) if chunk_count is not None else None,
    )


def _parse_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        dt = datetime.strptime(value, "%Y%m%dT%H%M%SZ")
        return dt.replace(tzinfo=timezone.utc)
    if "T" in value:
        dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
        return dt
    dt = datetime.strptime(value, "%Y%m%d")
    return dt


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        return value.strftime("%Y%m%dT%H%M%S")
    utc_value = value.astimezone(timezone.utc)
    return utc_value.strftime("%Y%m%dT%H%M%SZ")


def _default_token_fetcher(config: AppleCalendarConfig, payload: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover - relies on optional dependency
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - optional dependency path
        raise AuthorizationError("requests library is required for token exchange") from exc

    response = requests.post(config.token_endpoint, data=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def _default_caldav_factory(url: str, token: OAuthToken | None) -> Any:  # pragma: no cover - relies on optional dependency
    try:
        import caldav
    except ImportError as exc:  # pragma: no cover - optional dependency path
        raise AppleCalendarError("caldav library is required for Apple Calendar integration") from exc

    headers = {}
    if token:
        headers["Authorization"] = token.authorization_header()
    return caldav.DAVClient(url, headers=headers)
