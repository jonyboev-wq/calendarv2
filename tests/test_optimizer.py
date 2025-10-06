from datetime import datetime, timedelta


from src.scheduler.models import FixedEvent, FlexibleEvent
from src.scheduler.optimizer import (
    ScheduledBlock,
    schedule_events,
    update_schedule_after_completion,
)


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2024, 1, 1, hour, minute)


def test_schedule_prioritizes_importance_and_respects_fixed_blocks():
    day_start = _dt(9)
    day_end = _dt(17)

    lunch = FixedEvent("lunch", duration=timedelta(hours=1), importance=0, start=_dt(12))
    existing = [ScheduledBlock(start=lunch.start, end=lunch.end, event=lunch)]

    important = FlexibleEvent(
        "important",
        duration=timedelta(hours=1),
        importance=2.0,
        earliest_start=day_start,
        latest_finish=day_end,
    )
    normal = FlexibleEvent(
        "normal",
        duration=timedelta(hours=2),
        importance=1.0,
        earliest_start=day_start,
        latest_finish=day_end,
    )

    schedule = schedule_events(existing, [normal, important], day_start, day_end)

    important_block = next(block for block in schedule if block.event.event_id == "important")
    normal_block = next(block for block in schedule if block.event.event_id == "normal")

    assert important_block.start == day_start
    assert important_block.end == day_start + timedelta(hours=1)
    assert normal_block.start == day_start + timedelta(hours=1)
    assert normal_block.end == day_start + timedelta(hours=3)


def test_split_flexible_event_across_multiple_free_slots():
    day_start = _dt(9)
    day_end = _dt(17)

    blocker = FixedEvent("blocker", duration=timedelta(hours=1), importance=0, start=_dt(10))
    existing = [ScheduledBlock(start=blocker.start, end=blocker.end, event=blocker)]

    split_event = FlexibleEvent(
        "split",
        duration=timedelta(hours=3),
        importance=1.5,
        earliest_start=day_start,
        latest_finish=day_end,
        can_split=True,
        min_chunk=timedelta(hours=1),
    )

    schedule = schedule_events(existing, [split_event], day_start, day_end)
    blocks = [block for block in schedule if block.event.event_id == "split"]

    assert len(blocks) == 2
    assert blocks[0].start == day_start
    assert blocks[0].end == day_start + timedelta(hours=1)
    assert blocks[1].start == _dt(11)
    assert blocks[1].end == _dt(13)
    assert all(block.chunk_count == 2 for block in blocks)
    assert [block.chunk_index for block in blocks] == [1, 2]


def test_reschedule_after_early_completion():
    day_start = _dt(9)
    day_end = _dt(17)

    first = FlexibleEvent(
        "first",
        duration=timedelta(hours=2),
        importance=2.0,
        earliest_start=day_start,
        latest_finish=day_end,
    )
    second = FlexibleEvent(
        "second",
        duration=timedelta(hours=1),
        importance=1.0,
        earliest_start=day_start,
        latest_finish=day_end,
    )

    schedule = schedule_events([], [first, second], day_start, day_end)
    completed_at = day_start + timedelta(hours=1)

    updated = update_schedule_after_completion(schedule, "first", completed_at, day_start, day_end)

    first_block = next(block for block in updated if block.event.event_id == "first")
    second_block = next(block for block in updated if block.event.event_id == "second")

    assert first_block.start == day_start
    assert first_block.end == completed_at
    assert second_block.start == completed_at
    assert second_block.end == completed_at + timedelta(hours=1)
