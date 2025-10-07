from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.integrations.apple_calendar import (
    AppleCalendarClient,
    AppleCalendarConfig,
    AppleCalendarSyncService,
    CalendarEvent,
    CalendarSyncError,
    OAuthToken,
    _parse_ics,
)
from src.scheduler.models import FlexibleEvent


class FakeCalDavEvent:
    def __init__(self, ics: str) -> None:
        self.data = ics


class FakePersistedEvent:
    def __init__(self, calendar: "FakeCalendar", event: CalendarEvent) -> None:
        self._calendar = calendar
        self.data = event.to_ics()
        self.uid = event.uid
        self.saved = False

    def save(self) -> None:
        self.saved = True
        updated_event = _parse_ics(self.data)
        self._calendar._events[self.uid] = updated_event
        self._calendar._persisted[self.uid] = self
        self._calendar.updated_events.append(updated_event)


class FakeCalendar:
    def __init__(self, events: list[CalendarEvent] | None = None) -> None:
        self._events: dict[str, CalendarEvent] = {}
        self._persisted: dict[str, FakePersistedEvent] = {}
        self.added_events: list[CalendarEvent] = []
        self.updated_events: list[CalendarEvent] = []
        if events:
            for event in events:
                self._events[event.uid] = event
                self._persisted[event.uid] = FakePersistedEvent(self, event)

    # CalDAV API ------------------------------------------------------------------
    def date_search(self, start: datetime, end: datetime) -> list[FakeCalDavEvent]:
        return [FakeCalDavEvent(event.to_ics()) for event in self._events.values()]

    def event_by_uid(self, uid: str) -> FakePersistedEvent:
        if uid not in self._persisted:
            raise KeyError(uid)
        return self._persisted[uid]

    def save_event(self, payload: str | FakePersistedEvent) -> None:
        if isinstance(payload, FakePersistedEvent):
            event = _parse_ics(payload.data)
        else:
            event = _parse_ics(payload)
        self._events[event.uid] = event
        self._persisted[event.uid] = FakePersistedEvent(self, event)
        self.added_events.append(event)

    def add_event(self, payload: str) -> None:
        self.save_event(payload)


class FakePrincipal:
    def __init__(self, calendar: FakeCalendar) -> None:
        self._calendar = calendar

    def calendars(self) -> list[FakeCalendar]:
        return [self._calendar]


class FakeCalDavClient:
    def __init__(self, calendar: FakeCalendar) -> None:
        self._principal = FakePrincipal(calendar)

    def principal(self) -> FakePrincipal:
        return self._principal


def _make_client(calendar: FakeCalendar) -> AppleCalendarClient:
    config = AppleCalendarConfig(
        client_id="client",
        client_secret="secret",
        redirect_uri="https://example.com/callback",
        auth_endpoint="https://example.com/auth",
        token_endpoint="https://example.com/token",
        calendar_url="https://example.com/caldav",
    )
    caldav_client = FakeCalDavClient(calendar)
    client = AppleCalendarClient(
        config,
        caldav_client_factory=lambda url, token: caldav_client,
        token_fetcher=lambda cfg, payload: {
            "access_token": "token",
            "refresh_token": "refresh",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    )
    client._token = OAuthToken(  # type: ignore[attr-defined]
        access_token="token",
        refresh_token="refresh",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    return client


def test_load_events_parses_ics() -> None:
    start = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    event = CalendarEvent(
        uid="event-1",
        summary="Lecture",
        start=start,
        end=start + timedelta(hours=1),
    )
    calendar = FakeCalendar([event])
    client = _make_client(calendar)

    loaded = client.load_events(start - timedelta(hours=1), start + timedelta(hours=4))

    assert len(loaded) == 1
    assert loaded[0].uid == "event-1"
    assert loaded[0].summary == "Lecture"
    assert loaded[0].start == event.start
    assert loaded[0].end == event.end


def test_sync_creates_flexible_events_without_conflict() -> None:
    day_start = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    existing = CalendarEvent(
        uid="fixed-1",
        summary="Concert",
        start=day_start,
        end=day_start + timedelta(hours=1),
    )
    calendar = FakeCalendar([existing])
    client = _make_client(calendar)
    service = AppleCalendarSyncService(client)

    flexible = FlexibleEvent(
        event_id="task-1",
        duration=timedelta(hours=1),
        earliest_start=day_start + timedelta(hours=1),
        latest_finish=day_start + timedelta(hours=4),
    )

    synced = service.sync(start=day_start, end=day_start + timedelta(hours=8), flexible_events=[flexible])

    assert len(synced) == 1
    created = calendar.added_events[0]
    assert created.uid == "task-1"
    assert created.start == day_start + timedelta(hours=1)
    assert created.end == day_start + timedelta(hours=2)


def test_sync_detects_collisions_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    day_start = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    existing = CalendarEvent(
        uid="fixed-1",
        summary="Lecture",
        start=day_start,
        end=day_start + timedelta(hours=3),
    )
    calendar = FakeCalendar([existing])
    client = _make_client(calendar)
    service = AppleCalendarSyncService(client)

    impossible = FlexibleEvent(
        event_id="task-2",
        duration=timedelta(hours=2),
        earliest_start=day_start,
        latest_finish=day_start + timedelta(hours=1),
    )

    with pytest.raises(CalendarSyncError):
        service.sync(start=day_start, end=day_start + timedelta(hours=3), flexible_events=[impossible])

    assert any("collision" in record.message for record in caplog.records)


def test_create_or_update_event_updates_existing() -> None:
    day_start = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    original = CalendarEvent(
        uid="task-3",
        summary="Workout",
        start=day_start,
        end=day_start + timedelta(hours=1),
    )
    calendar = FakeCalendar([original])
    client = _make_client(calendar)

    updated_event = CalendarEvent(
        uid="task-3",
        summary="Workout",
        start=day_start + timedelta(hours=2),
        end=day_start + timedelta(hours=3),
        is_flexible=True,
    )

    client.create_or_update_event(updated_event)

    stored = calendar._events["task-3"]
    assert stored.start == updated_event.start
    assert stored.end == updated_event.end
    persisted = calendar.event_by_uid("task-3")
    assert persisted.saved is True
