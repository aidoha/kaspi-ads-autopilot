import { describe, it, expect } from "vitest";
import { areaPath, axisTicks, linePath, niceTicks, segments } from "./chart";

describe("геометрия графика", () => {
  it("разрыв в данных рвёт линию, а не соединяет через него", () => {
    // Дни без данных не приходят с сервера (окно календарное). Соединить
    // соседние точки прямой значило бы нарисовать динамику, которой не было.
    const pts = [
      { x: 0, y: 1 }, { x: 1, y: 2 }, { x: 5, y: 3 }, { x: 6, y: 4 },
    ];
    const segs = segments(pts, 1);
    expect(segs.length).toBe(2);
    expect(segs[0].length).toBe(2);
    expect(segs[1].length).toBe(2);
  });

  it("сплошной ряд остаётся одним сегментом", () => {
    const pts = [{ x: 0, y: 1 }, { x: 1, y: 2 }, { x: 2, y: 3 }];
    expect(segments(pts, 1).length).toBe(1);
  });

  it("путь не содержит NaN на плоском ряде", () => {
    const d = linePath([{ x: 0, y: 5 }, { x: 1, y: 5 }]);
    expect(d).not.toMatch(/NaN/);
  });
});

describe("круглые деления оси Y", () => {
  it("даёт человеческие числа вместо сырых min/max", () => {
    // Ровно тот случай со скриншота: домен 65..383 давал подписи
    // «0 / 191 / 383». Должно получиться 0/100/200/300/400.
    const a = niceTicks(65, 383, 5);
    expect(a.ticks).toEqual([0, 100, 200, 300, 400]);
    expect(a.min).toBe(0);
    expect(a.max).toBe(400);
  });

  it("домен накрывает данные целиком", () => {
    for (const [lo, hi] of [[3, 7], [0.012, 0.084], [1, 1_250_000], [0, 0.3]]) {
      const a = niceTicks(lo, hi, 5);
      expect(a.min).toBeLessThanOrEqual(lo);
      expect(a.max).toBeGreaterThanOrEqual(hi);
      expect(a.ticks[0]).toBe(a.min);
      expect(a.ticks[a.ticks.length - 1]).toBe(a.max);
    }
  });

  it("не оставляет пустую треть полотна над данными", () => {
    // TACoS до 21% давал шкалу 0/10/20/30: шаг промахивался на разряд, и
    // верхняя треть графика оставалась пустой.
    expect(niceTicks(0, 0.21, 5).ticks).toEqual([0, 0.05, 0.1, 0.15, 0.2, 0.25]);
    // CTR — доли процента: шаг обязан быть кратен 0,5%, иначе подписи
    // «0,3% / 0,5% / 0,8%» показывают равные интервалы разными.
    expect(niceTicks(0, 0.012, 5).ticks).toEqual([0, 0.005, 0.01, 0.015]);
  });

  it("доли не копят двоичную ошибку в подписях", () => {
    // TACoS приходит долями: шаг 0.05, сложенный десять раз, даёт
    // 0.15000000000000002 — и подпись оси показывает мусор.
    const a = niceTicks(0, 0.22, 5);
    for (const t of a.ticks) {
      expect(Number(t.toFixed(10))).toBe(t);
    }
  });

  it("плоский ряд не зацикливается и не делит на ноль", () => {
    // Ставка не менялась сутками — min === max.
    const a = niceTicks(40, 40, 5);
    expect(a.ticks.length).toBeGreaterThan(1);
    expect(a.max).toBeGreaterThan(a.min);
  });
});

describe("деления оси X", () => {
  it("падают на полночь Алматы, а не UTC", () => {
    // 2026-09-05 20:09 Алматы = 1788617340 UTC; три дня вперёд.
    const min = 1788617340, max = min + 3 * 86400;
    const ticks = axisTicks(min, max, [3600, 6 * 3600, 86400, 7 * 86400], -5 * 3600, 6);
    expect(ticks.length).toBeGreaterThan(0);
    for (const t of ticks) {
      expect((t + 5 * 3600) % 86400).toBe(0);   // ровно 00:00 по Алматы
      expect(t).toBeGreaterThanOrEqual(min);
      expect(t).toBeLessThanOrEqual(max);
    }
  });

  it("укрупняет шаг, чтобы подписи не наезжали друг на друга", () => {
    const steps = [1, 2, 3, 7, 14, 28];
    expect(axisTicks(0, 30, steps, 0, 6).length).toBeLessThanOrEqual(6);
    expect(axisTicks(0, 365, steps, 0, 6).length).toBeLessThanOrEqual(6);
  });
});

describe("заливка под линией", () => {
  it("замыкается на базовую линию, а не на произвольную точку", () => {
    const d = areaPath([{ x: 10, y: 20 }, { x: 30, y: 5 }], 100);
    expect(d).toBe("M10.0 20.0 L30.0 5.0 L30.0 100.0 L10.0 100.0 Z");
  });

  it("пустой сегмент не даёт NaN в пути", () => {
    expect(areaPath([], 100)).toBe("");
  });
});
