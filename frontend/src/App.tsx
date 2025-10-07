import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";
import {
  ApiBlock,
  ApiSchedule,
  EventPayload,
  ProposalPreview,
  createEvent,
  completeEvent,
  getProposal,
  getSchedule,
  updateSettings
} from "./api";

interface EventFormState {
  eventId: string;
  type: "fixed" | "flexible";
  durationMinutes: number;
  importance: number;
  start: string;
  earliestStart: string;
  latestFinish: string;
  canSplit: boolean;
  minChunkMinutes: number;
}

interface SettingsFormState {
  dayStart: string;
  dayEnd: string;
}

const defaultEventForm: EventFormState = {
  eventId: "",
  type: "flexible",
  durationMinutes: 60,
  importance: 1,
  start: "",
  earliestStart: "",
  latestFinish: "",
  canSplit: false,
  minChunkMinutes: 30
};

function formatDateTime(value: string): string {
  if (!value) return "—";
  const date = new Date(value);
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short"
  }).format(date);
}

function toIso(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  return date.toISOString();
}

function toLocalInput(value: string | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const tzOffset = date.getTimezoneOffset();
  const localDate = new Date(date.getTime() - tzOffset * 60000);
  return localDate.toISOString().slice(0, 16);
}

function buildEventPayload(form: EventFormState, includeId = true): EventPayload {
  const base: EventPayload = {
    type: form.type,
    duration_minutes: form.durationMinutes,
    importance: form.importance,
    can_split: form.type === "flexible" ? form.canSplit : false,
    min_chunk_minutes: form.minChunkMinutes
  };

  if (form.type === "fixed") {
    base.start = toIso(form.start);
  } else {
    base.earliest_start = toIso(form.earliestStart);
    base.latest_finish = toIso(form.latestFinish);
  }

  if (includeId) {
    base.event_id = form.eventId;
  }
  return base;
}

const App = () => {
  const [schedule, setSchedule] = useState<ApiSchedule | null>(null);
  const [form, setForm] = useState<EventFormState>(defaultEventForm);
  const [settingsForm, setSettingsForm] = useState<SettingsFormState>({
    dayStart: "",
    dayEnd: ""
  });
  const [proposal, setProposal] = useState<ProposalPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const fetchSchedule = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getSchedule();
      setSchedule(data);
      setSettingsForm({
        dayStart: toLocalInput(data.day_start),
        dayEnd: toLocalInput(data.day_end)
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSchedule();
  }, []);

  const handleFormChange = (key: keyof EventFormState) =>
    (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      const value =
        event.target.type === "checkbox"
          ? (event.target as HTMLInputElement).checked
          : event.target.value;
      setForm((prev) => ({
        ...prev,
        [key]:
          key === "durationMinutes" ||
          key === "importance" ||
          key === "minChunkMinutes"
            ? Number(value)
            : value
      }));
    };

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSuccess(null);

    if (!form.eventId.trim()) {
      setError("Введите идентификатор события");
      return;
    }

    try {
      const payload = buildEventPayload(form, true);
      const data = await createEvent(payload);
      setSchedule(data);
      setSuccess("Событие успешно добавлено");
      setForm({ ...defaultEventForm });
      setProposal(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleComplete = async (eventId: string) => {
    setError(null);
    setSuccess(null);
    try {
      const data = await completeEvent(eventId);
      setSchedule(data);
      setSuccess("Событие завершено, расписание обновлено");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleSettingsSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      const data = await updateSettings({
        day_start: toIso(settingsForm.dayStart),
        day_end: toIso(settingsForm.dayEnd)
      });
      setSchedule(data);
      setSuccess("Настройки рабочего окна сохранены");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleProposal = async () => {
    setError(null);
    setSuccess(null);
    try {
      const payload = buildEventPayload(form, false);
      const data = await getProposal(payload);
      setProposal(data);
      setSuccess("Получены рекомендуемые интервалы");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const activeBlocks = useMemo(() => schedule?.blocks ?? [], [schedule]);
  const freeWindows = useMemo(() => schedule?.free_windows ?? [], [schedule]);

  return (
    <div>
      <h1>Планировщик дня</h1>
      <p>Управляйте задачами, гибкостью выполнения и смотрите свободные окна в расписании.</p>

      {error ? <p className="error">{error}</p> : null}
      {success ? <p className="success">{success}</p> : null}

      <div className="flex-row">
        <section>
          <h2>Добавить событие</h2>
          <form onSubmit={handleCreate} data-testid="create-event-form">
            <label>
              Идентификатор
              <input
                value={form.eventId}
                onChange={handleFormChange("eventId")}
                placeholder="meeting-1"
                required
              />
            </label>

            <label>
              Тип
              <select value={form.type} onChange={handleFormChange("type")}>
                <option value="flexible">Гибкое</option>
                <option value="fixed">Фиксированное</option>
              </select>
            </label>

            <label>
              Длительность (мин)
              <input
                type="number"
                min={5}
                value={form.durationMinutes}
                onChange={handleFormChange("durationMinutes")}
              />
            </label>

            <label>
              Важность
              <input
                type="number"
                min={0}
                step={0.1}
                value={form.importance}
                onChange={handleFormChange("importance")}
              />
            </label>

            {form.type === "fixed" ? (
              <label>
                Начало
                <input
                  type="datetime-local"
                  value={form.start}
                  onChange={handleFormChange("start")}
                  required
                />
              </label>
            ) : (
              <>
                <label>
                  Самое раннее начало
                  <input
                    type="datetime-local"
                    value={form.earliestStart}
                    onChange={handleFormChange("earliestStart")}
                    required
                  />
                </label>
                <label>
                  Самое позднее окончание
                  <input
                    type="datetime-local"
                    value={form.latestFinish}
                    onChange={handleFormChange("latestFinish")}
                    required
                  />
                </label>
                <label>
                  Можно дробить
                  <input
                    type="checkbox"
                    checked={form.canSplit}
                    onChange={handleFormChange("canSplit")}
                  />
                </label>
                <label>
                  Минимальный кусочек (мин)
                  <input
                    type="number"
                    min={5}
                    value={form.minChunkMinutes}
                    onChange={handleFormChange("minChunkMinutes")}
                  />
                </label>
              </>
            )}

            <div className="flex-row">
              <button type="submit">Сохранить</button>
              <button type="button" onClick={handleProposal}>
                Подобрать окно
              </button>
            </div>
          </form>
        </section>

        <section>
          <h2>Настройки рабочего окна</h2>
          <form onSubmit={handleSettingsSubmit}>
            <label>
              Начало дня
              <input
                type="datetime-local"
                value={settingsForm.dayStart}
                onChange={(event) =>
                  setSettingsForm((prev) => ({ ...prev, dayStart: event.target.value }))
                }
              />
            </label>
            <label>
              Окончание дня
              <input
                type="datetime-local"
                value={settingsForm.dayEnd}
                onChange={(event) =>
                  setSettingsForm((prev) => ({ ...prev, dayEnd: event.target.value }))
                }
              />
            </label>
            <button type="submit">Обновить</button>
          </form>
        </section>
      </div>

      <section>
        <h2>Расписание</h2>
        {loading && <p>Загрузка...</p>}
        {!loading && activeBlocks.length === 0 ? <p>Нет запланированных задач.</p> : null}
        {activeBlocks.length > 0 && (
          <table className="table-like" data-testid="schedule-table">
            <thead>
              <tr>
                <th>Событие</th>
                <th>Начало</th>
                <th>Окончание</th>
                <th>Тип</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {activeBlocks.map((block) => (
                <tr key={`${block.event_id}-${block.start}-${block.end}`}>
                  <td>
                    <span className="badge">{block.event_id}</span>
                    {block.chunk_index ? (
                      <span className="badge">
                        часть {block.chunk_index}/{block.chunk_count}
                      </span>
                    ) : null}
                  </td>
                  <td>{formatDateTime(block.start)}</td>
                  <td>{formatDateTime(block.end)}</td>
                  <td>{block.type === "fixed" ? "Фикс" : "Гибкое"}</td>
                  <td>
                    <button type="button" onClick={() => handleComplete(block.event_id)}>
                      Завершить
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2>Свободные окна</h2>
        {freeWindows.length === 0 ? <p>Свободных окон нет.</p> : null}
        <ul>
          {freeWindows.map((window) => (
            <li key={`${window.start}-${window.end}`}>
              {formatDateTime(window.start)} — {formatDateTime(window.end)}
            </li>
          ))}
        </ul>
      </section>

      {proposal ? (
        <section>
          <h2>Рекомендации для текущей формы</h2>
          {proposal.blocks.length === 0 ? (
            <p>Нет подходящих интервалов.</p>
          ) : (
            <table className="table-like" data-testid="proposal-table">
              <thead>
                <tr>
                  <th>Начало</th>
                  <th>Окончание</th>
                  <th>Тип</th>
                </tr>
              </thead>
              <tbody>
                {proposal.blocks.map((block) => (
                  <tr key={`${block.event_id}-${block.start}`}>
                    <td>{formatDateTime(block.start)}</td>
                    <td>{formatDateTime(block.end)}</td>
                    <td>{block.type === "fixed" ? "Фикс" : "Гибкое"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      ) : null}
    </div>
  );
};

export default App;
