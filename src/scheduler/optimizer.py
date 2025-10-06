from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List, Sequence

from .models import Event, FixedEvent, FlexibleEvent


@dataclass(order=True)
class ScheduledBlock:
    """Represents a concrete time block occupied by an event."""

    start: datetime
    end: datetime
    event: Event
    chunk_index: int | None = None
    chunk_count: int | None = None

    def __post_init__(self) -> None:  # pragma: no cover - validation logic
        if self.end <= self.start:
            raise ValueError("Scheduled block must have positive duration")


def schedule_events(
    existing_blocks: Sequence[ScheduledBlock],
    candidates: Iterable[Event],
    day_start: datetime,
    day_end: datetime,
) -> List[ScheduledBlock]:
    """Schedule candidate events into the calendar.

    Existing blocks are treated as immutable and returned alongside the new blocks.
    """

    schedule = sorted(existing_blocks, key=lambda block: block.start)
    events = sorted(candidates, key=lambda event: (-event.importance, event.duration))

    for event in events:
        if isinstance(event, FixedEvent):
            block = ScheduledBlock(event.start, event.end, event)
            _ensure_free(schedule, block.start, block.end)
            schedule.append(block)
            schedule.sort(key=lambda block: block.start)
            continue

        if isinstance(event, FlexibleEvent):
            blocks = _schedule_flexible(event, schedule, day_start, day_end)
            schedule.extend(blocks)
            schedule.sort(key=lambda block: block.start)
            continue

        raise TypeError(f"Unsupported event type: {type(event)!r}")

    return schedule


def update_schedule_after_completion(
    schedule: Sequence[ScheduledBlock],
    event_id: str,
    completion_time: datetime,
    day_start: datetime,
    day_end: datetime,
) -> List[ScheduledBlock]:
    """Free time after an early completion and pull flexible events forward."""

    base_blocks: List[ScheduledBlock] = []
    reschedule_events: dict[str, FlexibleEvent] = {}

    for block in sorted(schedule, key=lambda blk: blk.start):
        if block.event.event_id == event_id:
            if completion_time <= block.start:
                continue
            if completion_time < block.end:
                base_blocks.append(
                    ScheduledBlock(
                        start=block.start,
                        end=completion_time,
                        event=block.event,
                        chunk_index=block.chunk_index,
                        chunk_count=block.chunk_count,
                    )
                )
            else:
                base_blocks.append(block)
            continue

        if isinstance(block.event, FlexibleEvent) and block.start >= completion_time:
            reschedule_events[block.event.event_id] = block.event
            continue

        base_blocks.append(block)

    updated_schedule = schedule_events(
        base_blocks,
        reschedule_events.values(),
        day_start,
        day_end,
    )

    return sorted(updated_schedule, key=lambda blk: blk.start)


def _schedule_flexible(
    event: FlexibleEvent,
    schedule: Sequence[ScheduledBlock],
    day_start: datetime,
    day_end: datetime,
) -> List[ScheduledBlock]:
    free_intervals = _free_intervals(schedule, day_start, day_end)
    relevant = [
        (
            max(interval_start, event.earliest_start),
            min(interval_end, event.latest_finish),
        )
        for interval_start, interval_end in free_intervals
    ]
    relevant = [interval for interval in relevant if interval[1] > interval[0]]

    if not event.can_split:
        duration = event.duration
        for start, end in relevant:
            if end - start >= duration:
                block = ScheduledBlock(start, start + duration, event)
                _ensure_free(schedule, block.start, block.end)
                return [block]
        raise ValueError(f"Cannot schedule event {event.event_id}")

    remaining = event.duration
    blocks: List[ScheduledBlock] = []
    for start, end in relevant:
        cursor = start
        while cursor < end and remaining > timedelta(0):
            available = end - cursor
            chunk = min(available, remaining)
            if remaining > event.min_chunk and chunk < event.min_chunk:
                break
            if chunk < event.min_chunk and remaining <= event.min_chunk:
                chunk = remaining
            if chunk < event.min_chunk and remaining > event.min_chunk:
                break

            block = ScheduledBlock(cursor, cursor + chunk, event)
            _ensure_free(schedule + blocks, block.start, block.end)
            blocks.append(block)
            remaining -= chunk
            cursor += chunk

        if remaining <= timedelta(0):
            break

    if remaining > timedelta(0):
        raise ValueError(f"Cannot split event {event.event_id} within its window")

    for index, block in enumerate(blocks, start=1):
        blocks[index - 1] = ScheduledBlock(
            start=block.start,
            end=block.end,
            event=block.event,
            chunk_index=index,
            chunk_count=len(blocks),
        )

    return blocks


def _free_intervals(
    schedule: Sequence[ScheduledBlock],
    day_start: datetime,
    day_end: datetime,
) -> List[tuple[datetime, datetime]]:
    cursor = day_start
    intervals: List[tuple[datetime, datetime]] = []
    for block in sorted(schedule, key=lambda blk: blk.start):
        if block.start > cursor:
            intervals.append((cursor, block.start))
        cursor = max(cursor, block.end)
    if cursor < day_end:
        intervals.append((cursor, day_end))
    return intervals


def _ensure_free(
    schedule: Sequence[ScheduledBlock],
    start: datetime,
    end: datetime,
) -> None:
    for block in schedule:
        latest_start = max(block.start, start)
        earliest_end = min(block.end, end)
        if latest_start < earliest_end:
            raise ValueError("Time slot is already occupied")
