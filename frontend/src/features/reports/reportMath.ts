// Чистая арифметика экрана отчётов: стрелки к прошлому периоду, раскладка
// выручки «куда уходит», сортировка, подписи периодов. Вынесено, чтобы
// покрыть тестами — здесь ошибка тихая и про деньги.
import type { ReportProduct } from "./types";

export type Delta = { pct: number; dir: "up" | "down" | "flat" };

/** Изменение к прошлому периоду. null — сравнивать не с чем (прошлое
 *  неизвестно или было нулём: «+∞%» владельцу ничего не скажет). */
export function delta(cur: number | null, prev: number | null): Delta | null {
  if (cur === null || prev === null || prev === 0) return null;
  const pct = (cur - prev) / Math.abs(prev);
  if (Math.abs(pct) < 0.005) return { pct: 0, dir: "flat" };
  return { pct, dir: pct > 0 ? "up" : "down" };
}

export type Segment = {
  key: "cogs" | "commission" | "delivery" | "ads" | "bonus" | "profit";
  label: string;
  value: number;
  /** Доля полосы 0..1. */
  share: number;
};

export const SEGMENT_LABELS: Record<Segment["key"], string> = {
  cogs: "Себестоимость",
  commission: "Комиссия Kaspi",
  delivery: "Доставка",
  ads: "Реклама",
  bonus: "Бонусы клиентам",
  profit: "Прибыль",
};

/** Раскладка выручки товара на статьи. Полоса — от выручки; если расходы
 *  её превысили (убыток), полосой становятся сами расходы, иначе сегменты
 *  вылезли бы за 100%. Неизвестные статьи (нет себестоимости) пропускаются. */
export function breakdown(p: ReportProduct): Segment[] {
  const parts: [Segment["key"], number | null][] = [
    ["cogs", p.cogs],
    ["commission", p.commission],
    ["delivery", p.delivery],
    ["ads", p.ad_cost],
    ["bonus", p.bonus],
  ];
  const known = parts.filter(([, v]) => v !== null && v > 0) as [Segment["key"], number][];
  const costs = known.reduce((s, [, v]) => s + v, 0);
  if (p.profit !== null && p.profit > 0) known.push(["profit", p.profit]);
  const base = Math.max(p.revenue, costs);
  if (base <= 0) return [];
  return known.map(([key, value]) => ({ key, label: SEGMENT_LABELS[key], value, share: value / base }));
}

export type SortKey = "name" | "revenue" | "orders" | "ad_cost" | "bonus" | "profit" | "margin";

/** null всегда в конце, в какую сторону ни сортируй: «неизвестно» не
 *  должно всплывать наверх ни как лучший, ни как худший товар. */
export function sortProducts(list: ReportProduct[], key: SortKey, desc: boolean): ReportProduct[] {
  const out = [...list];
  out.sort((a, b) => {
    if (key === "name") {
      const c = (a.name || "").localeCompare(b.name || "", "ru");
      return desc ? -c : c;
    }
    const va = a[key], vb = b[key];
    if (va === null && vb === null) return 0;
    if (va === null) return 1;
    if (vb === null) return -1;
    return desc ? vb - va : va - vb;
  });
  return out;
}

const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

/** «2026-10-13», «2026-10-19» → «13–19 окт»; через месяц — «28 сен – 4 окт». */
export function periodLabel(start: string, end: string): string {
  const [ys, ms, ds] = start.split("-").map(Number);
  const [ye, me, de] = end.split("-").map(Number);
  if (start === end) return `${ds} ${MONTHS[ms - 1]}`;
  if (ys === ye && ms === me) return `${ds}–${de} ${MONTHS[me - 1]}`;
  const yearTail = ys !== ye ? ` ${ye}` : "";
  return `${ds} ${MONTHS[ms - 1]} – ${de} ${MONTHS[me - 1]}${yearTail}`;
}

/** Ввод поля «комиссия, %» → доля для API. "" → null (наследовать). */
export function parsePercent(s: string): number | null | "bad" {
  const t = s.trim().replace(",", ".");
  if (!t) return null;
  const n = Number(t);
  if (!Number.isFinite(n) || n < 0 || n > 90) return "bad";
  return n / 100;
}

/** Ввод поля «себестоимость, ₸» → число. Пробелы-разделители тысяч терпим. */
export function parseMoney(s: string): number | null | "bad" {
  const t = s.replace(/[\s ]/g, "").replace(",", ".");
  if (!t) return null;
  const n = Number(t);
  if (!Number.isFinite(n) || n < 0) return "bad";
  return n;
}
