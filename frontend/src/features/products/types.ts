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
