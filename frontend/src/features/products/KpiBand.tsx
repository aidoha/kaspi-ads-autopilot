import { fmtMoney, fmtPct, fmtX } from "../../api/format";
import type { Campaign, Overview } from "./types";

type Props = {
  overview: Overview;
  campaign: string;
  campaigns: Campaign[];
  budgetsAvailable: boolean;
  /** Среди товаров текущего фильтра есть хоть один, ведущийся в нескольких
   *  кампаниях — тогда выручка в KPI не привязана к выбранной кампании. */
  hasMultiCampaignProduct: boolean;
};

/** Строка KPI из макета: хайрлайны, без карточек, разделители между
 *  ячейками (.kpis/.kpi, docs/design/autopilot-mockup.html). */
export default function KpiBand({
  overview, campaign, campaigns, budgetsAvailable, hasMultiCampaignProduct,
}: Props) {
  const t = overview.totals;
  const budget = campaigns.find((c) => c.id === campaign)?.daily_budget ?? null;

  const showAttributionNote = campaign !== "all" && hasMultiCampaignProduct;

  return (
    <>
      <div className="kpis">
        <div className="kpi">
          <div className="kpi-label">Расход</div>
          <div className="kpi-val"><span className="cur">₸</span> {fmtMoney(t.cost)}</div>
          {budgetsAvailable && budget !== null && (
            <div className="kpi-note">из {fmtMoney(budget)} ₸ бюджета в день</div>
          )}
        </div>
        <div className="kpi">
          <div className="kpi-label">Выручка</div>
          <div className="kpi-val"><span className="cur">₸</span> {fmtMoney(t.revenue)}</div>
          <div className="kpi-note">Shop API, минус отмены</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">TACoS</div>
          <div className="kpi-val">{fmtPct(t.tacos)}</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">ROAS</div>
          <div className="kpi-val">{fmtX(t.roas)}</div>
          <div className="kpi-note">по GMV кабинета {fmtX(t.roas_gmv)}</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Клики → корзины</div>
          <div className="kpi-val">
            {fmtMoney(t.clicks)} <span className="cur">→ {fmtMoney(t.carts)}</span>
          </div>
          <div className="kpi-note">конверсия {fmtPct(t.cr)}</div>
        </div>
      </div>
      {showAttributionNote && (
        <p className="kpi-note" style={{ margin: "8px 0 0" }}>
          Выручка показана полностью по товару: она приходит по товару и к
          кампаниям не привязана.
        </p>
      )}
    </>
  );
}
