"""REST API for interacting with the scheduling engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field, root_validator

from ..scheduler.models import Event, FixedEvent, FlexibleEvent
from ..scheduler.optimizer import (
    ScheduledBlock,
    schedule_events,
    update_schedule_after_completion,
)


def _default_day_bounds(reference: Optional[datetime] = None) -> tuple[datetime, datetime]:
    reference = reference or datetime.utcnow()
    start = reference.replace(hour=8, minute=0, second=0, microsecond=0)
    end = reference.replace(hour=20, minute=0, second=0, microsecond=0)
    if end <= start:
        end = start + timedelta(hours=12)
    return start, end


@dataclass
class _SchedulerState:
    """Container for persisted scheduler state."""

    day_start: datetime
    day_end: datetime
    events: Dict[str, Event] = field(default_factory=dict)
    blocks: List[ScheduledBlock] = field(default_factory=list)

    def reschedule(self) -> None:
        """Re-run the optimizer for all known events."""

        try:
            self.blocks = schedule_events(
                existing_blocks=(),
                candidates=self.events.values(),
                day_start=self.day_start,
                day_end=self.day_end,
            )
        except ValueError as exc:  # pragma: no cover - validation bubble up
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    def upsert_event(self, event_id: str, event: Event) -> None:
        self.events[event_id] = event
        self.reschedule()

    def delete_event(self, event_id: str) -> None:
        if event_id in self.events:
            del self.events[event_id]
            self.reschedule()

    def complete_event(self, event_id: str, completion_time: datetime) -> None:
        if event_id not in self.events:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Event '{event_id}' was not found.",
            )
        self.blocks = update_schedule_after_completion(
            schedule=self.blocks,
            event_id=event_id,
            completion_time=completion_time,
            day_start=self.day_start,
            day_end=self.day_end,
        )
        del self.events[event_id]

    def free_windows(self) -> List[dict[str, datetime]]:
        cursor = self.day_start
        windows: List[dict[str, datetime]] = []
        for block in sorted(self.blocks, key=lambda blk: blk.start):
            if block.start > cursor:
                windows.append({"start": cursor, "end": block.start})
            cursor = max(cursor, block.end)
        if cursor < self.day_end:
            windows.append({"start": cursor, "end": self.day_end})
        return windows


class EventPayload(BaseModel):
    """Shared payload for fixed and flexible events."""

    type: Literal["fixed", "flexible"]
    duration_minutes: int = Field(gt=0)
    importance: float = Field(default=1.0, ge=0.0)
    start: Optional[datetime] = Field(default=None, description="Start time for fixed events")
    earliest_start: Optional[datetime] = Field(
        default=None, description="Earliest allowed start for flexible events"
    )
    latest_finish: Optional[datetime] = Field(
        default=None, description="Latest allowed finish for flexible events"
    )
    can_split: bool = Field(default=False)
    min_chunk_minutes: int = Field(default=30, gt=0)

    @root_validator
    def _validate_event(cls, values: dict[str, object]) -> dict[str, object]:
        event_type = values.get("type")
        if event_type == "fixed":
            if values.get("start") is None:
                raise ValueError("Fixed events require a 'start' timestamp")
        elif event_type == "flexible":
            if values.get("earliest_start") is None or values.get("latest_finish") is None:
                raise ValueError("Flexible events require 'earliest_start' and 'latest_finish'")
        return values

    def build_event(self, event_id: str) -> Event:
        duration = timedelta(minutes=self.duration_minutes)
        if self.type == "fixed":
            assert self.start is not None  # for type checkers
            return FixedEvent(
                event_id=event_id,
                start=self.start,
                duration=duration,
                importance=self.importance,
            )
        assert self.earliest_start is not None
        assert self.latest_finish is not None
        return FlexibleEvent(
            event_id=event_id,
            duration=duration,
            importance=self.importance,
            earliest_start=self.earliest_start,
            latest_finish=self.latest_finish,
            can_split=self.can_split,
            min_chunk=timedelta(minutes=self.min_chunk_minutes),
        )


class EventCreateRequest(EventPayload):
    event_id: str = Field(min_length=1)


class EventUpdateRequest(EventPayload):
    ...


class EventResponse(BaseModel):
    event_id: str
    type: Literal["fixed", "flexible"]
    duration_minutes: float
    importance: float
    details: dict[str, object]


class BlockResponse(BaseModel):
    start: datetime
    end: datetime
    event_id: str
    type: Literal["fixed", "flexible"]
    chunk_index: Optional[int]
    chunk_count: Optional[int]


class ScheduleResponse(BaseModel):
    day_start: datetime
    day_end: datetime
    events: List[EventResponse]
    blocks: List[BlockResponse]
    free_windows: List[dict[str, datetime]]


class CompletionRequest(BaseModel):
    completion_time: datetime


class SettingsUpdate(BaseModel):
    day_start: datetime
    day_end: datetime

    @root_validator
    def _check_bounds(cls, values: dict[str, object]) -> dict[str, object]:
        start = values.get("day_start")
        end = values.get("day_end")
        if isinstance(start, datetime) and isinstance(end, datetime) and end <= start:
            raise ValueError("day_end must be after day_start")
        return values


class ProposalRequest(EventPayload):
    event_id: Optional[str] = None


class ProposalResponse(BaseModel):
    blocks: List[BlockResponse]
    free_windows: List[dict[str, datetime]]


def _serialize_event(event: Event) -> EventResponse:
    if isinstance(event, FixedEvent):
        details = {"start": event.start}
        event_type: Literal["fixed", "flexible"] = "fixed"
    elif isinstance(event, FlexibleEvent):
        details = {
            "earliest_start": event.earliest_start,
            "latest_finish": event.latest_finish,
            "can_split": event.can_split,
            "min_chunk": event.min_chunk,
        }
        event_type = "flexible"
    else:  # pragma: no cover - future proofing
        raise TypeError(f"Unsupported event type: {type(event)!r}")
    return EventResponse(
        event_id=event.event_id,
        type=event_type,
        duration_minutes=event.duration.total_seconds() / 60,
        importance=event.importance,
        details=details,
    )


def _serialize_block(block: ScheduledBlock) -> BlockResponse:
    event = block.event
    event_type: Literal["fixed", "flexible"] = "fixed" if isinstance(event, FixedEvent) else "flexible"
    return BlockResponse(
        start=block.start,
        end=block.end,
        event_id=event.event_id,
        type=event_type,
        chunk_index=block.chunk_index,
        chunk_count=block.chunk_count,
    )


def create_app() -> FastAPI:
    day_start, day_end = _default_day_bounds()
    state = _SchedulerState(day_start=day_start, day_end=day_end)

    app = FastAPI(title="Calendar Scheduler API")

    @app.get("/api/schedule", response_model=ScheduleResponse)
    def get_schedule() -> ScheduleResponse:
        events = [_serialize_event(event) for event in state.events.values()]
        blocks = [_serialize_block(block) for block in state.blocks]
        return ScheduleResponse(
            day_start=state.day_start,
            day_end=state.day_end,
            events=events,
            blocks=blocks,
            free_windows=state.free_windows(),
        )

    @app.post("/api/events", status_code=status.HTTP_201_CREATED, response_model=ScheduleResponse)
    def create_event(payload: EventCreateRequest) -> ScheduleResponse:
        if payload.event_id in state.events:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Event '{payload.event_id}' already exists.",
            )
        event = payload.build_event(payload.event_id)
        state.upsert_event(payload.event_id, event)
        return get_schedule()

    @app.put("/api/events/{event_id}", response_model=ScheduleResponse)
    def update_event(event_id: str, payload: EventUpdateRequest) -> ScheduleResponse:
        if event_id not in state.events:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Event '{event_id}' was not found.",
            )
        event = payload.build_event(event_id)
        state.upsert_event(event_id, event)
        return get_schedule()

    @app.delete("/api/events/{event_id}", response_model=ScheduleResponse)
    def delete_event(event_id: str) -> ScheduleResponse:
        if event_id not in state.events:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Event '{event_id}' was not found.",
            )
        state.delete_event(event_id)
        return get_schedule()

    @app.post("/api/events/{event_id}/complete", response_model=ScheduleResponse)
    def complete_event(event_id: str, payload: CompletionRequest) -> ScheduleResponse:
        state.complete_event(event_id, payload.completion_time)
        return get_schedule()

    @app.put("/api/settings", response_model=ScheduleResponse)
    def update_settings(payload: SettingsUpdate) -> ScheduleResponse:
        state.day_start = payload.day_start
        state.day_end = payload.day_end
        state.reschedule()
        return get_schedule()

    @app.post("/api/proposals", response_model=ProposalResponse)
    def propose_slot(payload: ProposalRequest) -> ProposalResponse:
        event_id = payload.event_id or "__proposal__"
        event = payload.build_event(event_id)
        try:
            preview_schedule = schedule_events(
                existing_blocks=state.blocks,
                candidates=[event],
                day_start=state.day_start,
                day_end=state.day_end,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        preview_blocks = [
            block for block in preview_schedule if block.event.event_id == event.event_id
        ]
        return ProposalResponse(
            blocks=[_serialize_block(block) for block in preview_blocks],
            free_windows=state.free_windows(),
        )

    return app


app = create_app()
