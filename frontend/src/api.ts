export interface ApiEvent {
  event_id: string;
  type: "fixed" | "flexible";
  duration_minutes: number;
  importance: number;
  details: Record<string, unknown>;
}

export interface ApiBlock {
  start: string;
  end: string;
  event_id: string;
  type: "fixed" | "flexible";
  chunk_index?: number | null;
  chunk_count?: number | null;
}

export interface ApiSchedule {
  day_start: string;
  day_end: string;
  events: ApiEvent[];
  blocks: ApiBlock[];
  free_windows: { start: string; end: string }[];
}

export interface ProposalPreview {
  blocks: ApiBlock[];
  free_windows: { start: string; end: string }[];
}

export interface EventPayload {
  event_id?: string;
  type: "fixed" | "flexible";
  duration_minutes: number;
  importance: number;
  can_split?: boolean;
  min_chunk_minutes?: number;
  start?: string | undefined;
  earliest_start?: string | undefined;
  latest_finish?: string | undefined;
}

export interface SettingsPayload {
  day_start?: string | undefined;
  day_end?: string | undefined;
}

export type ApiRuntimeMode = "http" | "mock";

const resolveInitialMode = (): {
  mode: ApiRuntimeMode;
  allowAutoFallback: boolean;
} => {
  const explicitMode = (import.meta.env.VITE_API_MODE ?? "").toLowerCase();
  const legacyFlag = (import.meta.env.VITE_USE_MOCK_API ?? "").toLowerCase();

  if (explicitMode === "mock" || legacyFlag === "true") {
    return { mode: "mock", allowAutoFallback: false };
  }

  if (explicitMode === "http" || legacyFlag === "false") {
    return { mode: "http", allowAutoFallback: false };
  }

  return { mode: "http", allowAutoFallback: true };
};

const initial = resolveInitialMode();
let runtimeMode: ApiRuntimeMode = initial.mode;
const allowAutoFallback = initial.allowAutoFallback;

const modeListeners = new Set<(mode: ApiRuntimeMode) => void>();

const notifyModeChange = () => {
  for (const listener of modeListeners) {
    listener(runtimeMode);
  }
};

export const subscribeToApiMode = (listener: (mode: ApiRuntimeMode) => void) => {
  modeListeners.add(listener);
  listener(runtimeMode);
  return () => {
    modeListeners.delete(listener);
  };
};

export const getCurrentApiMode = (): ApiRuntimeMode => runtimeMode;

class BackendUnavailableError extends Error {}

const clone = <T>(value: T): T =>
  typeof structuredClone === "function"
    ? structuredClone(value)
    : JSON.parse(JSON.stringify(value));

const apiFetch = async <T>(path: string, init?: RequestInit, fallbackMessage?: string): Promise<T> => {
  try {
    const response = await fetch(path, init);
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new Error((data as { detail?: string } | null)?.detail ?? fallbackMessage ?? "Запрос завершился ошибкой");
    }
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof BackendUnavailableError) {
      throw error;
    }
    if (error instanceof TypeError || error instanceof AggregateError) {
      throw new BackendUnavailableError(fallbackMessage ?? "Бэкенд недоступен");
    }
    if (error instanceof Error) {
      throw error;
    }
    throw new BackendUnavailableError(fallbackMessage ?? "Не удалось выполнить запрос");
  }
};

interface MockState {
  schedule: ApiSchedule;
}

const createMockState = (): MockState => {
  const now = new Date();
  const dayStart = new Date(now);
  dayStart.setHours(8, 0, 0, 0);
  const dayEnd = new Date(now);
  dayEnd.setHours(20, 0, 0, 0);

  const trainingStart = new Date(dayStart);
  trainingStart.setHours(10, 0, 0, 0);
  const trainingEnd = new Date(trainingStart.getTime() + 60 * 60 * 1000);

  const lectureStart = new Date(dayStart);
  lectureStart.setHours(13, 0, 0, 0);
  const lectureEnd = new Date(lectureStart.getTime() + 90 * 60 * 1000);

  const schedule: ApiSchedule = {
    day_start: dayStart.toISOString(),
    day_end: dayEnd.toISOString(),
    events: [
      {
        event_id: "training",
        type: "fixed",
        duration_minutes: 60,
        importance: 1,
        details: {}
      },
      {
        event_id: "lecture",
        type: "fixed",
        duration_minutes: 90,
        importance: 1,
        details: {}
      }
    ],
    blocks: [
      {
        event_id: "training",
        type: "fixed",
        start: trainingStart.toISOString(),
        end: trainingEnd.toISOString()
      },
      {
        event_id: "lecture",
        type: "fixed",
        start: lectureStart.toISOString(),
        end: lectureEnd.toISOString()
      }
    ],
    free_windows: []
  };

  schedule.free_windows = computeFreeWindows(schedule);

  return { schedule };
};

const computeFreeWindows = (schedule: ApiSchedule) => {
  const windows: { start: string; end: string }[] = [];
  const sortedBlocks = [...schedule.blocks].sort(
    (a, b) => new Date(a.start).getTime() - new Date(b.start).getTime()
  );

  let cursor = new Date(schedule.day_start);
  const endOfDay = new Date(schedule.day_end);

  for (const block of sortedBlocks) {
    const blockStart = new Date(block.start);
    if (blockStart.getTime() > cursor.getTime()) {
      windows.push({ start: cursor.toISOString(), end: blockStart.toISOString() });
    }
    const blockEnd = new Date(block.end);
    if (blockEnd.getTime() > cursor.getTime()) {
      cursor = blockEnd;
    }
  }

  if (cursor.getTime() < endOfDay.getTime()) {
    windows.push({ start: cursor.toISOString(), end: endOfDay.toISOString() });
  }

  return windows;
};

const mockState = createMockState();

const placeFlexibleEvent = (payload: EventPayload, schedule: ApiSchedule): { start: string; end: string } => {
  const durationMs = payload.duration_minutes * 60 * 1000;
  const earliest = payload.earliest_start ? new Date(payload.earliest_start) : new Date(schedule.day_start);
  const latest = payload.latest_finish ? new Date(payload.latest_finish) : new Date(schedule.day_end);

  for (const window of schedule.free_windows) {
    const windowStart = new Date(window.start);
    const windowEnd = new Date(window.end);

    const candidateStart = new Date(Math.max(windowStart.getTime(), earliest.getTime()));
    const candidateEnd = new Date(candidateStart.getTime() + durationMs);

    if (candidateEnd.getTime() <= windowEnd.getTime() && candidateEnd.getTime() <= latest.getTime()) {
      return { start: candidateStart.toISOString(), end: candidateEnd.toISOString() };
    }
  }

  // Если не нашли подходящее окно, ставим событие сразу после последнего блока
  const fallbackStart = schedule.blocks.length
    ? new Date(schedule.blocks[schedule.blocks.length - 1].end)
    : new Date(schedule.day_start);
  const fallbackEnd = new Date(fallbackStart.getTime() + durationMs);
  return { start: fallbackStart.toISOString(), end: fallbackEnd.toISOString() };
};

const mockCreateEvent = (payload: EventPayload): ApiSchedule => {
  if (!payload.event_id) {
    throw new Error("Для мок-режима требуется идентификатор события");
  }

  const schedule = mockState.schedule;
  const baseEvent: ApiEvent = {
    event_id: payload.event_id,
    type: payload.type,
    duration_minutes: payload.duration_minutes,
    importance: payload.importance,
    details: {}
  };

  let block: ApiBlock;
  if (payload.type === "fixed") {
    const start = payload.start ? new Date(payload.start) : new Date(schedule.day_start);
    const end = new Date(start.getTime() + payload.duration_minutes * 60 * 1000);
    block = {
      event_id: baseEvent.event_id,
      type: "fixed",
      start: start.toISOString(),
      end: end.toISOString()
    };
  } else {
    const placement = placeFlexibleEvent(payload, schedule);
    block = {
      event_id: baseEvent.event_id,
      type: "flexible",
      start: placement.start,
      end: placement.end
    };
  }

  schedule.events = schedule.events.filter((event) => event.event_id !== baseEvent.event_id).concat(baseEvent);
  schedule.blocks = schedule.blocks
    .filter((existing) => existing.event_id !== baseEvent.event_id)
    .concat(block)
    .sort((a, b) => new Date(a.start).getTime() - new Date(b.start).getTime());
  schedule.free_windows = computeFreeWindows(schedule);

  return clone(schedule);
};

const mockCompleteEvent = (eventId: string): ApiSchedule => {
  const schedule = mockState.schedule;
  schedule.events = schedule.events.filter((event) => event.event_id !== eventId);
  schedule.blocks = schedule.blocks.filter((block) => block.event_id !== eventId);
  schedule.free_windows = computeFreeWindows(schedule);
  return clone(schedule);
};

const mockUpdateSettings = (payload: SettingsPayload): ApiSchedule => {
  const schedule = mockState.schedule;
  if (payload.day_start) {
    schedule.day_start = payload.day_start;
  }
  if (payload.day_end) {
    schedule.day_end = payload.day_end;
  }
  schedule.free_windows = computeFreeWindows(schedule);
  return clone(schedule);
};

const mockProposal = (payload: EventPayload): ProposalPreview => {
  const schedule = mockState.schedule;
  if (payload.type === "flexible") {
    const placement = placeFlexibleEvent(payload, schedule);
    return {
      blocks: [
        {
          event_id: payload.event_id ?? "proposal",
          type: "flexible",
          start: placement.start,
          end: placement.end
        }
      ],
      free_windows: clone(schedule.free_windows)
    };
  }
  return { blocks: [], free_windows: clone(schedule.free_windows) };
};

const callWithFallback = async <T>(
  httpCall: () => Promise<T>,
  mockCall: () => T | Promise<T>
): Promise<T> => {
  if (runtimeMode === "mock") {
    return await Promise.resolve(mockCall());
  }

  try {
    const result = await httpCall();
    if (runtimeMode !== "http") {
      runtimeMode = "http";
      notifyModeChange();
    }
    return result;
  } catch (error) {
    if (allowAutoFallback && error instanceof BackendUnavailableError) {
      console.warn("[api] Backend недоступен, переключаемся на демонстрационный режим");
      runtimeMode = "mock";
      notifyModeChange();
      return await Promise.resolve(mockCall());
    }
    throw error;
  }
};

export const getSchedule = async (): Promise<ApiSchedule> => {
  return callWithFallback(
    () => apiFetch<ApiSchedule>("/api/schedule", undefined, "Не удалось загрузить расписание"),
    () => clone(mockState.schedule)
  );
};

export const createEvent = async (payload: EventPayload): Promise<ApiSchedule> => {
  return callWithFallback(
    () =>
      apiFetch<ApiSchedule>(
        "/api/events",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        },
        "Не удалось создать событие"
      ),
    () => mockCreateEvent(payload)
  );
};

export const completeEvent = async (eventId: string): Promise<ApiSchedule> => {
  return callWithFallback(
    () =>
      apiFetch<ApiSchedule>(
        `/api/events/${eventId}/complete`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ completion_time: new Date().toISOString() })
        },
        "Не удалось завершить событие"
      ),
    () => mockCompleteEvent(eventId)
  );
};

export const updateSettings = async (payload: SettingsPayload): Promise<ApiSchedule> => {
  return callWithFallback(
    () =>
      apiFetch<ApiSchedule>(
        "/api/settings",
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        },
        "Не удалось обновить настройки"
      ),
    () => mockUpdateSettings(payload)
  );
};

export const getProposal = async (payload: EventPayload): Promise<ProposalPreview> => {
  return callWithFallback(
    () =>
      apiFetch<ProposalPreview>(
        "/api/proposals",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        },
        "Не удалось получить рекомендации"
      ),
    () => mockProposal(payload)
  );
};
