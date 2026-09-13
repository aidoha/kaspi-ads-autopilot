import { describe, it, expect } from "vitest";
import { segments, linePath } from "./chart";

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
