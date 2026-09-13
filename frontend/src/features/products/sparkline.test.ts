import { describe, it, expect } from "vitest";
import { sparkPath } from "../../components/Sparkline";

describe("геометрия спарклайна", () => {
  it("пустой ряд не даёт пути вместо падения", () => {
    expect(sparkPath([], 84, 24)).toBe("");
    expect(sparkPath([5], 84, 24)).toBe("");
  });

  it("плоский ряд рисуется линией, а не делением на ноль", () => {
    // Ставка часто не меняется сутками. min === max, и наивная формула
    // (v-min)/(max-min) дала бы NaN, а путь стал бы невидимым.
    const d = sparkPath([10, 10, 10], 84, 24);
    expect(d).toMatch(/^M/);
    expect(d).not.toMatch(/NaN/);
  });

  it("точки идут слева направо и умещаются в рамку", () => {
    const d = sparkPath([1, 5, 3], 100, 20);
    const xs = [...d.matchAll(/[ML](-?[\d.]+)/g)].map((m) => Number(m[1]));
    expect(xs).toEqual([...xs].sort((a, b) => a - b));
    expect(Math.min(...xs)).toBeGreaterThanOrEqual(0);
    expect(Math.max(...xs)).toBeLessThanOrEqual(100);
  });
});
