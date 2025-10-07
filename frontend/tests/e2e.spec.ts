import { test, expect } from "@playwright/test";

test.describe("Планировщик дня", () => {
  test("создание гибкого события отображает его в расписании", async ({ page }) => {
    const dayStart = "2024-01-01T06:00:00.000Z";
    const dayEnd = "2024-01-01T18:00:00.000Z";

    const initialSchedule = {
      day_start: dayStart,
      day_end: dayEnd,
      events: [],
      blocks: [],
      free_windows: [{ start: dayStart, end: dayEnd }]
    };

    const updatedSchedule = {
      day_start: dayStart,
      day_end: dayEnd,
      events: [
        {
          event_id: "deep-work",
          type: "flexible",
          duration_minutes: 120,
          importance: 1,
          details: {
            earliest_start: "2024-01-01T07:00:00.000Z",
            latest_finish: "2024-01-01T12:00:00.000Z",
            can_split: false,
            min_chunk: "0:30:00"
          }
        }
      ],
      blocks: [
        {
          start: "2024-01-01T07:00:00.000Z",
          end: "2024-01-01T09:00:00.000Z",
          event_id: "deep-work",
          type: "flexible",
          chunk_index: null,
          chunk_count: null
        }
      ],
      free_windows: [
        { start: "2024-01-01T06:00:00.000Z", end: "2024-01-01T07:00:00.000Z" },
        { start: "2024-01-01T09:00:00.000Z", end: "2024-01-01T18:00:00.000Z" }
      ]
    };

    let scheduleCalls = 0;

    await page.route("**/api/schedule", async (route) => {
      scheduleCalls += 1;
      if (scheduleCalls === 1) {
        await route.fulfill({ json: initialSchedule });
      } else {
        await route.fulfill({ json: updatedSchedule });
      }
    });

    await page.route("**/api/events", async (route) => {
      expect(route.request().method()).toBe("POST");
      await route.fulfill({ status: 201, json: updatedSchedule });
    });

    await page.goto("/");

    await expect(page.getByText("Свободные окна")).toBeVisible();
    await expect(page.getByRole("list")).toContainText("06.01.2024");

    await page.getByLabel("Идентификатор").fill("deep-work");
    await page.getByLabel("Длительность (мин)").fill("120");
    await page.getByLabel("Самое раннее начало").fill("2024-01-01T09:00");
    await page.getByLabel("Самое позднее окончание").fill("2024-01-01T12:00");

    await page.getByRole("button", { name: "Сохранить" }).click();

    await expect(page.getByTestId("schedule-table")).toContainText("deep-work");
    await expect(page.getByText("Событие успешно добавлено")).toBeVisible();
    await expect(page.getByTestId("schedule-table")).toContainText("Гибкое");
  });
});
