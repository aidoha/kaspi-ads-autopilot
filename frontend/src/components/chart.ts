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

/** «Круглые» деления оси Y и подогнанный под них домен. Раньше подписи
 *  брались как min/середина/max сырого диапазона — на графике это давало
 *  «383 ₸ / 191 ₸ / 0 ₸»: числа, которые ничего не значат и которые глаз не
 *  может использовать как линейку. Шаг выбирается из 1/2/2.5/5×10^k, домен
 *  расширяется до ближайших делений, так что верх и низ графика всегда
 *  лежат на подписанной линии.
 *
 *  Ноль включается всегда: все величины панели (деньги, доли, проценты)
 *  неотрицательны, а заливка под линией без нулевой базы врёт — площадь
 *  считается от произвольной отсечки.
 *
 *  `maxTicks` — предел ЧИСЛА ИНТЕРВАЛОВ, а не пожелание: домен растягивается
 *  до круглых границ, и мелкий шаг тем самым исключается, а не подгоняется. */
export function niceTicks(min: number, max: number, maxTicks = 5): { min: number; max: number; ticks: number[] } {
  const lo = Math.min(0, min);
  // Плоский ряд (ставка не менялась сутками) дал бы шаг 0 и бесконечный
  // цикл ниже — поднимаем потолок до чего-то ненулевого.
  const hi = max > lo ? max : lo + 1;
  const mag = Math.pow(10, Math.floor(Math.log10(hi - lo)));
  // Лестница 1/2/5 без 2.5: доли в панели печатаются с одним знаком после
  // запятой (fmtPct), и шаг 0,25% дал бы подписи «0,3% / 0,5% / 0,8%» —
  // одинаковые интервалы, выглядящие разными.
  // Берём ПЕРВЫЙ шаг снизу, при котором делений не больше нужного, а не
  // шаг «примерно span/count»: последний легко промахивается на разряд и
  // оставляет пустую треть полотна (0–30% там, где данные не выше 21%).
  let step = mag;
  outer:
  for (let e = -1; e <= 2; e++) {
    for (const m of [1, 2, 5]) {
      const s = m * mag * Math.pow(10, e);
      if (Math.ceil(hi / s) - Math.floor(lo / s) <= maxTicks) { step = s; break outer; }
    }
  }
  const dMin = Math.floor(lo / step) * step;
  const dMax = Math.ceil(hi / step) * step;
  // Округление до разрядности шага. Ни сложение, ни умножение долей от этого
  // не спасают: 3 × 0.1 в двоичной плавающей точке даёт 0.30000000000000004,
  // и подпись оси показывает мусор вместо «30,0%».
  const dp = Math.max(0, -Math.floor(Math.log10(step)) + 1);
  const round = (v: number) => Number(v.toFixed(dp));
  const ticks: number[] = [];
  for (let i = 0; dMin + i * step <= dMax + step * 1e-9; i++) ticks.push(round(dMin + i * step));
  return { min: round(dMin), max: round(dMax), ticks };
}

/** Деления оси X по круглым границам вместо «первая/средняя/последняя точка
 *  ряда» (из-за которого под графиком стояло «05.09, 20:09» — момент первого
 *  тика, а не дата, по которой можно ориентироваться).
 *
 *  `steps` — допустимые шаги в единицах оси, от мелкого к крупному: секунды
 *  для тиков, дни для подневных рядов. `origin` сдвигает решётку: для тиков
 *  это −18000, чтобы деления падали на полночь Алматы (UTC+5), а не UTC. */
export function axisTicks(min: number, max: number, steps: number[], origin = 0, maxCount = 6): number[] {
  const span = max - min;
  const biggest = steps[steps.length - 1];
  // Если не подходит даже самый крупный шаг из списка — кратно укрупняем его,
  // а не сдаёмся на нём: иначе длинный период (год по дням при списке до 28)
  // выдал бы четырнадцать делений, налезающих друг на друга.
  const step = steps.find((s) => span / s <= maxCount)
    ?? biggest * Math.ceil(span / maxCount / biggest);
  const out: number[] = [];
  const first = Math.ceil((min - origin) / step) * step + origin;
  for (let t = first; t <= max; t += step) out.push(t);
  return out;
}

/** Путь заливки под линией: сама ломаная, затем вниз к базовой линии и
 *  обратно. Заливка рисуется посегментно — общий контур через разрыв
 *  залил бы дни, которых в данных нет. */
export function areaPath(points: Point[], baseY: number): string {
  if (points.length === 0) return "";
  const first = points[0], last = points[points.length - 1];
  return `${linePath(points)} L${last.x.toFixed(1)} ${baseY.toFixed(1)} L${first.x.toFixed(1)} ${baseY.toFixed(1)} Z`;
}
