// Типы ровно по контракту API (см. шапку плана задачи) — не расширять
// полями, которых сервер не присылает.

export type Campaign = {
  id: string;
  name: string;
  daily_budget: number | null;
};

export type CampaignsResponse = {
  campaigns: Campaign[];
  budgets_available: boolean;
};

export type OverviewTotals = {
  cost: number | null;
  revenue: number | null;
  gmv: number | null;
  clicks: number | null;
  carts: number | null;
  tacos: number | null;
  roas: number | null;
  roas_gmv: number | null;
  cr: number | null;
};

export type Overview = {
  day: string;
  days: number;
  last_snapshot_ts: number | null;
  totals: OverviewTotals;
};

export type Product = {
  sku: string;
  merchant_sku: string;
  campaign_ids: string[];
  name: string | null;
  bid: number | null;
  cost: number | null;
  revenue: number | null;
  clicks: number | null;
  carts: number | null;
  tacos: number | null;
  roas: number | null;
  ctr: number | null;
  cr: number | null;
  enabled: boolean;
  status: string;
  bid_spark: number[];
};

export type ProductsResponse = {
  products: Product[];
};

/** Расписание биддера для товара в конкретной кампании. days_mask — биты
 *  Пн..Вс = 0..6 (core/daypart.py, как datetime.weekday()). */
export type ProductControl = {
  enabled: boolean;
  window_start: number;
  window_end: number;
  days_mask: number;
};

/** Одна строка decisions_log (core/store.py). old_bid/new_bid — ставка до
 *  и после решения; при action="hold" обычно совпадают. */
export type Decision = {
  ts: number;
  day: string;
  sku: string;
  merchant_sku: string;
  old_bid: number | null;
  new_bid: number | null;
  action: "raise" | "lower" | "hold";
  loop: string;
  reason: string;
  applied: number;
  campaign_id: string;
};

/** GET /api/products/{cid}/{sku}. values — эффективный конфиг (глобал →
 *  кампания → товар), всегда числа. owned — поля, заданные ИМЕННО на уровне
 *  этого товара (см. task-3-brief: не путать со «значение отличается от
 *  глобального» — совпасть оно может и при переопределении). */
export type ProductDetail = {
  sku: string;
  campaign_id: string;
  name: string | null;
  values: Record<string, number>;
  owned: string[];
  control: ProductControl;
  decisions: Decision[];
};

/** POST .../preview — предсказание, ничего не отправлено и не изменено.
 *  null — по товару ещё нет снапшота; control — решение контрольного слоя
 *  (выключен/вне окна), до правил дело не дошло; fast+slow — оба контура
 *  конкурируют, важны оба (core/preview.py). */
export type PreviewLoop = { action: "raise" | "lower" | "hold"; reason: string };
export type Preview =
  | null
  | { control: PreviewLoop }
  | { fast: PreviewLoop; slow: PreviewLoop };

/** GET .../series — ряды для графиков вкладки «Графики». Две шкалы времени
 *  в разных массивах (см. task-4-brief): ticks — внутридневные тики
 *  воркера (несколько точек в сутки), daily — ровно одна точка на день.
 *  Смешивать их в одну ось X нельзя. */
export type Tick = { ts: number; bid: number | null; avg_cpc: number | null };

/** Подневная точка. Поля — null, когда величина не определена (например
 *  revenue, если Shop API ещё не опрашивали за день) — это дырка на
 *  графике, а не ноль. */
export type DailyPoint = {
  day: string;
  cost: number | null;
  revenue: number | null;
  gmv: number | null;
  views: number | null;
  clicks: number | null;
  carts: number | null;
  transactions: number | null;
  tacos: number | null;
  roas: number | null;
  roas_gmv: number | null;
  ctr: number | null;
  cr: number | null;
};

/** Правка биддера — маркер на графике ставки. */
export type DecisionMarker = {
  ts: number;
  action: "raise" | "lower" | "hold";
  old_bid: number | null;
  new_bid: number | null;
  reason: string;
};

/** Целевой коридор TACoS эффективного конфига товара. Доли (0..1), не проценты. */
export type Corridor = { low: number; high: number };

export type ProductSeries = {
  ticks: Tick[];
  daily: DailyPoint[];
  decisions: DecisionMarker[];
  corridor: Corridor;
};
