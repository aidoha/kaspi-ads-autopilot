import { describe, it, expect } from "vitest";
import { breakdown, delta, parseMoney, parsePercent, periodLabel, sortProducts } from "./reportMath";
import type { ReportProduct } from "./types";

function prod(over: Partial<ReportProduct>): ReportProduct {
  return {
    key: "A", merchant_sku: "A", kaspi_skus: [], name: "A", revenue: 0, orders: 0, units: 0,
    avg_check: null, cancelled: 0, returned: 0, delivery: 0, ad_cost: 0, ad_views: 0,
    ad_clicks: 0, ad_orders: 0, ad_revenue: 0, ctr: null, cpc: null, drr: null, tacos: null,
    bonus: 0, bonus_orders: 0, bonus_revenue: 0, has_ads: false, has_bonus: false,
    unit_cost: null, commission_pct: null, commission_source: null, commission: null,
    cogs: null, marketing: 0, profit: null, margin: null, needs_cost: true, ...over,
  };
}

describe("delta", () => {
  it("рост и падение в долях от прошлого", () => {
    expect(delta(120, 100)).toEqual({ pct: 0.2, dir: "up" });
    expect(delta(80, 100)?.dir).toBe("down");
  });
  it("не с чем сравнить — null, а не бесконечность", () => {
    expect(delta(100, 0)).toBeNull();
    expect(delta(100, null)).toBeNull();
    expect(delta(null, 100)).toBeNull();
  });
  it("убыток, ставший меньше, — это рост", () => {
    expect(delta(-50, -100)?.dir).toBe("up");
  });
});

describe("breakdown", () => {
  it("сегменты от выручки, остаток — прибыль", () => {
    const s = breakdown(prod({ revenue: 1000, cogs: 500, commission: 100, delivery: 0,
      ad_cost: 100, bonus: 0, profit: 300 }));
    expect(s.map((x) => x.key)).toEqual(["cogs", "commission", "ads", "profit"]);
    expect(s.reduce((a, x) => a + x.share, 0)).toBeCloseTo(1);
  });
  it("убыток: полоса по расходам, за 100% не вылезает", () => {
    const s = breakdown(prod({ revenue: 100, cogs: 150, commission: 10, ad_cost: 40, profit: -100 }));
    expect(s.reduce((a, x) => a + x.share, 0)).toBeCloseTo(1);
    expect(s.find((x) => x.key === "profit")).toBeUndefined();
  });
  it("без продаж и расходов — пусто", () => {
    expect(breakdown(prod({}))).toEqual([]);
  });
});

describe("sortProducts", () => {
  it("неизвестная прибыль — в конце в обе стороны", () => {
    const list = [prod({ key: "a", profit: null }), prod({ key: "b", profit: 5 }), prod({ key: "c", profit: -5 })];
    expect(sortProducts(list, "profit", true).map((p) => p.key)).toEqual(["b", "c", "a"]);
    expect(sortProducts(list, "profit", false).map((p) => p.key)).toEqual(["c", "b", "a"]);
  });
});

describe("periodLabel", () => {
  it("внутри месяца и через границу", () => {
    expect(periodLabel("2026-10-13", "2026-10-19")).toBe("13–19 окт");
    expect(periodLabel("2026-09-28", "2026-10-04")).toBe("28 сен – 4 окт");
    expect(periodLabel("2026-10-19", "2026-10-19")).toBe("19 окт");
  });
});

describe("ввод чисел", () => {
  it("проценты → доля, пусто → null", () => {
    expect(parsePercent("12,5")).toBeCloseTo(0.125);
    expect(parsePercent("")).toBeNull();
    expect(parsePercent("120")).toBe("bad");
  });
  it("деньги с пробелами тысяч", () => {
    expect(parseMoney("12 500")).toBe(12500);
    expect(parseMoney("-1")).toBe("bad");
    expect(parseMoney(" ")).toBeNull();
  });
});
