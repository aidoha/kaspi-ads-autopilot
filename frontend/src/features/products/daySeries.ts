// Сведение внутридневных тиков воркера к одной точке на сутки. Вынесено
// отдельным модулем с тестом: ошибка здесь рисует молча неверную картинку
// (съехавшие сутки, ноль вместо дырки), и на глаз её не поймать.

import type { Tick, DecisionMarker } from "./types";
import type { ChartMarker } from "../../components/LineChart";

/** Алматы — UTC+5 круглый год (в Казахстане нет перевода часов). Биддер
 *  живёт по этому поясу, и сутки графика обязаны совпадать с сутками его
 *  расписания, иначе ночной сброс ставки попадёт не в те сутки. */
export const ALMATY_OFFSET_SEC = 5 * 3600;

/** Эпоха-секунды → номер суток Алматы. Та же шкала, что у dayIndex() для
 *  подневных рядов (дни от эпохи), поэтому обе можно рисовать одинаково. */
export function tickDay(ts: number): number {
  return Math.floor((ts + ALMATY_OFFSET_SEC) / 86400);
}

/** Медиана. Именно она, а не среднее: биддер держит рабочую ставку почти
 *  весь день, а ночью дейпарт роняет её в пол — треть суток на минималке
 *  утянула бы среднее вниз и показала уровень, которого не было. */
function median(values: number[]): number {
  const v = [...values].sort((a, b) => a - b);
  const mid = v.length >> 1;
  return v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2;
}

/** Тики → точки «сутки → медиана». Сутки без единого значения в ряд не
 *  попадают вовсе (а не приходят нулём): это дырка в данных, и линия на
 *  ней обязана порваться. */
export function dailyMedian(ticks: Tick[], pick: (t: Tick) => number | null): { x: number; y: number }[] {
  const byDay = new Map<number, number[]>();
  for (const t of ticks) {
    const v = pick(t);
    if (v === null || !Number.isFinite(v)) continue;
    const d = tickDay(t.ts);
    const bucket = byDay.get(d);
    if (bucket) bucket.push(v); else byDay.set(d, [v]);
  }
  return [...byDay.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([x, vals]) => ({ x, y: median(vals) }));
}

/** Правки биддера → маркеры на суточной оси.
 *
 *  За сутки правок бывает несколько, и на суточной шкале они встали бы в
 *  одну точку друг на друга. Поэтому на сутки приходится максимум два
 *  маркера — «были подъёмы» и «были снижения», — разведённые по X на
 *  толщину значка; причины всех правок этого направления едут в подсказку
 *  одной строкой. Складывать их в «итог за сутки» нельзя: подъём и
 *  снижение в один день — это два разных события, а не ноль. */
export function dailyMarkers(decisions: DecisionMarker[]): ChartMarker[] {
  type Group = { bids: number[]; reasons: string[] };
  const byKey = new Map<string, Group>();
  for (const d of decisions) {
    if (d.action === "hold" || d.new_bid === null) continue;
    const key = `${tickDay(d.ts)}:${d.action}`;
    const g = byKey.get(key) ?? { bids: [], reasons: [] };
    g.bids.push(d.new_bid);
    if (!g.reasons.includes(d.reason)) g.reasons.push(d.reason);
    byKey.set(key, g);
  }
  const out: ChartMarker[] = [];
  for (const [key, g] of byKey) {
    const [dayStr, action] = key.split(":");
    const day = Number(dayStr);
    const kind = action as "raise" | "lower";
    // Развод по X — только когда в эти сутки есть и подъёмы, и снижения.
    const both = byKey.has(`${day}:${kind === "raise" ? "lower" : "raise"}`);
    const dx = both ? (kind === "raise" ? -0.14 : 0.14) : 0;
    const n = g.bids.length;
    out.push({
      x: day + dx,
      y: g.bids[g.bids.length - 1],
      kind,
      label: n > 1 ? `${n} ${plural(n)}: ${g.reasons.join("; ")}` : g.reasons[0],
    });
  }
  return out.sort((a, b) => a.x - b.x);
}

function plural(n: number): string {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "правка";
  if (mod10 >= 2 && mod10 <= 4 && !(mod100 >= 12 && mod100 <= 14)) return "правки";
  return "правок";
}
