// Формат чисел панели. Вынесено отдельным модулем, потому что это
// единственная часть фронта, которую имеет смысл покрывать тестами:
// здесь легко ошибиться молча, и ошибка будет про деньги.

const NBSP = " ";

/** null — величина не определена. Прочерк, никогда не ноль. */
export function fmtMoney(n: number | null): string {
  if (n === null || n === undefined) return "—";
  return Math.round(n).toLocaleString("ru-RU").replace(/\s/g, NBSP);
}

/** Доли приходят из API как 0..1. */
export function fmtPct(n: number | null): string {
  if (n === null || n === undefined) return "—";
  return (n * 100).toLocaleString("ru-RU", {
    minimumFractionDigits: 1, maximumFractionDigits: 1,
  }) + "%";
}

export function fmtX(n: number | null): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("ru-RU", {
    minimumFractionDigits: 1, maximumFractionDigits: 1,
  }) + "×";
}

/** Эпоха-секунды → время Алматы. Явная зона, а не зона браузера: владелец
 *  может открыть панель из другого пояса, и «обновлено 14:32» должно
 *  означать 14:32 в Алматы, где работает биддер. */
export function fmtTs(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("ru-RU", {
    timeZone: "Asia/Almaty",
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

/** Эпоха-секунды → только время Алматы (для строк ленты решений, где дата
 *  и так «сегодня» — весь список decisions приходит за один день). */
export function fmtTime(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("ru-RU", {
    timeZone: "Asia/Almaty", hour: "2-digit", minute: "2-digit",
  });
}

/** Пороги здоровья TACoS — те же, что показывала старая панель. */
export function tacosHealth(t: number | null): "good" | "warn" | "crit" | "na" {
  if (t === null || t === undefined) return "na";
  if (t < 0.10) return "good";
  if (t < 0.18) return "warn";
  return "crit";
}
