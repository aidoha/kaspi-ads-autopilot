// Ответы /api/reports/* — поля один в один с core/reports.py.
// Деньги в тенге, доли 0..1, null — величина не определена (не ноль).

export type ReportProduct = {
  key: string;
  merchant_sku: string | null;
  kaspi_skus: string[];
  name: string;
  revenue: number;
  orders: number;
  units: number;
  avg_check: number | null;
  cancelled: number;
  returned: number;
  delivery: number;
  ad_cost: number;
  ad_views: number;
  ad_clicks: number;
  ad_orders: number;
  ad_revenue: number;
  ctr: number | null;
  cpc: number | null;
  drr: number | null;
  tacos: number | null;
  bonus: number;
  bonus_orders: number;
  bonus_revenue: number;
  has_ads: boolean;
  has_bonus: boolean;
  unit_cost: number | null;
  commission_pct: number | null;
  commission_source: "product" | "default" | null;
  commission: number | null;
  cogs: number | null;
  marketing: number;
  profit: number | null;
  margin: number | null;
  needs_cost: boolean;
};

export type Totals = {
  revenue: number;
  orders: number;
  units: number;
  cancelled: number;
  returned: number;
  delivery: number;
  ad_cost: number | null;
  ad_revenue: number;
  ad_orders: number;
  bonus: number | null;
  tacos: number | null;
  profit: number | null;
  margin: number | null;
  profit_missing: number;
};

export type Upload = {
  id: number;
  kind: string;
  kind_label: string;
  filename: string;
  period_start: string | null;
  period_end: string | null;
  rows: number;
  user: string;
  ts: number;
};

export type Period = { start: string; end: string; files: number };

export type BonusCampaign = {
  name: string;
  start_date: string;
  status: string;
  views: number;
  clicks: number;
  orders: number;
  revenue: number;
  bonus: number;
};

export type Dashboard =
  | { empty: true; periods: Period[]; uploads: Upload[] }
  | {
      empty: false;
      start: string;
      end: string;
      prev: { start: string; end: string; totals: Totals };
      totals: Totals;
      products: ReportProduct[];
      campaigns: BonusCampaign[];
      default_commission_pct: number | null;
      orders_range: { start: string | null; end: string | null };
      warnings: string[];
      has_ads: boolean;
      has_bonus: boolean;
      periods: Period[];
      uploads: Upload[];
    };

export type UploadResult = {
  kind: string;
  kind_label: string;
  period_start: string | null;
  period_end: string | null;
  rows: number;
  duplicate: boolean;
  upload_id: number;
};
