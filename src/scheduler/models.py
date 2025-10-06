from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Event:
    """Base class for calendar events."""

    event_id: str
    duration: timedelta
    importance: float = field(default=1.0, kw_only=True)

    def __post_init__(self) -> None:  # pragma: no cover - validation logic
        if self.duration <= timedelta(0):
            raise ValueError("Duration must be positive")
        if self.importance < 0:
            raise ValueError("Importance must be non-negative")


@dataclass(frozen=True)
class FlexibleEvent(Event):
    """Event that can move within a window and optionally split."""

    earliest_start: datetime
    latest_finish: datetime
    can_split: bool = False
    min_chunk: timedelta = field(default=timedelta(minutes=30))

    def __post_init__(self) -> None:  # pragma: no cover - validation logic
        super().__post_init__()
        if self.latest_finish <= self.earliest_start:
            raise ValueError("latest_finish must be after earliest_start")
        if self.can_split and self.min_chunk <= timedelta(0):
            raise ValueError("min_chunk must be positive when splitting is allowed")


@dataclass(frozen=True)
class FixedEvent(Event):
    """Event that must occur at a specific time."""

    start: datetime

    def __post_init__(self) -> None:  # pragma: no cover - validation logic
        super().__post_init__()

    @property
    def end(self) -> datetime:
        return self.start + self.duration
