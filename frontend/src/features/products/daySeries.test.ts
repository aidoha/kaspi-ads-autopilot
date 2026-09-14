import { describe, it, expect } from "vitest";
import { dailyMarkers, dailyMedian, tickDay } from "./daySeries";
import type { Tick, DecisionMarker } from "./types";

// 2026-09-10 00:00 Алматы = 1789318800 UTC. От него и пляшем.
const D10 = 1789318800;
const day = (n: number) => tickDay(D10) + n;

function ticksForDay(dayOffsetSec: number, bids: [number | null, number | null][]): Tick[] {
  return bids.map(([bid, cpc], i) => ({
    ts: D10 + dayOffsetSec + i * 300, bid, avg_cpc: cpc,
  }));
}

describe("сведение тиков к суткам", () => {
  it("даёт одну точку на сутки, а не тысячи тиков", () => {
    const ticks = [...ticksForDay(0, [[100, 70], [180, 120], [180, 120]]),
                   ...ticksForDay(86400, [[200, 140], [200, 140]])];
    const out = dailyMedian(ticks, (t) => t.bid);
    expect(out).toEqual([{ x: day(0), y: 180 }, { x: day(1), y: 200 }]);
  });

  it("медиана не съезжает от ночного пола дейпарта", () => {
    // Треть суток ставка в полу (12), две трети — рабочие 180. Среднее
    // утянуло бы вниз, медиана держит рабочий уровень.
    const ticks = ticksForDay(0, [[12, null], [12, null], [180, 120], [180, 120], [180, 120]]);
    expect(dailyMedian(ticks, (t) => t.bid)).toEqual([{ x: day(0), y: 180 }]);
  });

  it("сутки без значений выпадают (дырка), а не приходят нулём", () => {
    const ticks = [...ticksForDay(0, [[100, 70]]),
                   ...ticksForDay(86400, [[null, null]]),   // сутки без ставки
                   ...ticksForDay(2 * 86400, [[120, 80]])];
    const out = dailyMedian(ticks, (t) => t.bid);
    expect(out.map((p) => p.x)).toEqual([day(0), day(2)]);   // day(1) выпал
  });

  it("сутки Алматы, а не UTC: тик в 02:00 местного = те же сутки", () => {
    // 21:00 UTC 09-09 = 02:00 Алматы 10-09 — должен попасть в сутки 10-го.
    const t: Tick[] = [{ ts: D10 - 3 * 3600, bid: 50, avg_cpc: null }];
    expect(dailyMedian(t, (x) => x.bid)).toEqual([{ x: day(0), y: 50 }]);
  });
});

describe("маркеры решений на суточной оси", () => {
  const mk = (dayOffset: number, action: "raise" | "lower", newBid: number, reason: string): DecisionMarker =>
    ({ ts: D10 + dayOffset * 86400 + 3600, action, old_bid: 0, new_bid: newBid, reason });

  it("несколько правок одного направления за сутки — один маркер со счётчиком", () => {
    const out = dailyMarkers([mk(0, "raise", 100, "score 5"), mk(0, "raise", 120, "score 6")]);
    expect(out).toHaveLength(1);
    expect(out[0].kind).toBe("raise");
    expect(out[0].y).toBe(120);                 // ставка последней правки
    expect(out[0].label).toContain("2 правки");
  });

  it("подъём и снижение в один день — два маркера, разведённые по X", () => {
    const out = dailyMarkers([mk(0, "raise", 100, "вверх"), mk(0, "lower", 80, "вниз")]);
    expect(out).toHaveLength(2);
    expect(out[0].x).not.toBe(out[1].x);        // не слиплись в точку
    expect(Math.round(out[0].x)).toBe(Math.round(out[1].x));  // но те же сутки
  });

  it("hold и правки без new_bid игнорируются", () => {
    const out = dailyMarkers([
      { ts: D10, action: "hold", old_bid: 50, new_bid: 50, reason: "держим" },
      { ts: D10, action: "raise", old_bid: 50, new_bid: null, reason: "нет ставки" },
    ]);
    expect(out).toEqual([]);
  });
});
