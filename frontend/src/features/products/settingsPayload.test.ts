import { describe, it, expect } from "vitest";
import { buildSettingsPayload } from "./settingsPayload";

describe("сборка тела PUT .../settings", () => {
  it("включённое «наследовать» шлёт null, а не 0 — иначе потолок ставки станет 0 ₸", () => {
    const out = buildSettingsPayload({
      bid_ceiling: { value: "0", inherited: true },
    } as any);
    expect(out.bid_ceiling).toBeNull();
  });

  it("снятое «наследовать» шлёт значение из поля как есть", () => {
    const out = buildSettingsPayload({
      bid_ceiling: { value: "45", inherited: false },
    } as any);
    expect(out.bid_ceiling).toBe("45");
  });

  it("смешанный набор полей — независимые решения по каждому", () => {
    const out = buildSettingsPayload({
      bid_ceiling: { value: "45", inherited: false },
      min_bid: { value: "12", inherited: true },
    } as any);
    expect(out).toEqual({ bid_ceiling: "45", min_bid: null });
  });
});
