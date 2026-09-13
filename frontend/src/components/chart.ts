// Чистая геометрия графиков — перенос из docs/design/autopilot-mockup.html
// (функция lineChart). Вынесена отдельно от LineChart.tsx, чтобы разрывы
// в данных и путь линии можно было проверить тестом: ошибка здесь рисует
// молча неверную картинку (соединённый разрыв, NaN в d), и её никто не
// заметит на глаз сразу.

export type Point = { x: number; y: number };

/** Линейная шкала значений в пиксели: min → 0, max → size (без инверсии —
 *  для оси Y компонент сам разворачивает, т.к. в SVG y растёт вниз).
 *  `pad` — доля запаса по краям диапазона (0.16 в макете), чтобы линия
 *  не упиралась в край графика; для оси X передаётся 0.
 *  `clampMin` — не пускать нижнюю границу ниже нуля (деньги, доли и проценты
 *  в этом проекте отрицательными не бывают — как в макете, где
 *  `min = Math.max(0, min - pad)`). Для оси X не передаётся: там ноль
 *  ничего не значит (тик или день-индекс). */
export function scale(values: number[], size: number, pad = 0.16, clampMin = false): { min: number; max: number; toPx: (v: number) => number } {
  const finite = values.filter((v) => Number.isFinite(v));
  let min = finite.length ? Math.min(...finite) : 0;
  let max = finite.length ? Math.max(...finite) : 1;
  const span0 = max - min;
  const p = span0 * pad || (pad > 0 ? 1 : 0);
  min -= p;
  max += p;
  if (clampMin) min = Math.max(0, min);
  const span = max - min || 1;
  return { min, max, toPx: (v: number) => ((v - min) / span) * size };
}

/** Разбивает ряд точек на непрерывные куски: новый сегмент начинается,
 *  если расстояние между соседними точками по X больше maxGap. Дни без
 *  метрик просто не приходят с сервера (окно календарное) — соединить
 *  соседние точки прямой через пропуск значило бы нарисовать динамику,
 *  которой не было. Каждый сегмент рисуется своим <path>. */
export function segments(points: Point[], maxGap: number): Point[][] {
  if (points.length === 0) return [];
  const out: Point[][] = [];
  let cur: Point[] = [points[0]];
  for (let i = 1; i < points.length; i++) {
    const gap = points[i].x - points[i - 1].x;
    if (gap > maxGap) {
      out.push(cur);
      cur = [];
    }
    cur.push(points[i]);
  }
  out.push(cur);
  return out;
}

/** SVG-путь ломаной через точки одного сегмента (без разрывов внутри). */
export function linePath(points: Point[]): string {
  return points
    .map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)} ${p.y.toFixed(1)}`)
    .join(" ");
}
