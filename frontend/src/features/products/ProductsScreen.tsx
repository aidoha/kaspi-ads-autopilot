import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend, ApiError } from "../../api/client";
import { fmtTs } from "../../api/format";
import Segmented from "../../components/Segmented";
import "../../styles/products.css";
import KpiBand from "./KpiBand";
import ProductRow from "./ProductRow";
import type { Campaign, CampaignsResponse, Overview, Product, ProductsResponse } from "./types";

type Period = "7" | "14" | "30";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; campaigns: Campaign[]; budgetsAvailable: boolean; overview: Overview; products: Product[] };

type Props = {
  /** В макете нет кнопки выхода — экран входа появляется отдельно, а сессия
   *  живёт на куке. Кнопка нужна чтобы владелец мог выйти вообще откуда-то;
   *  выносим её в шапку минимальным элементом, не нарушая вёрстку макета. */
  onLogout: () => void;
};

/** Главный экран: шапка с фильтрами, строка KPI, список товаров с
 *  тогглерами. Разметка и стили — из утверждённого макета
 *  docs/design/autopilot-mockup.html. */
export default function ProductsScreen({ onLogout }: Props) {
  const [campaign, setCampaign] = useState("all");
  const [period, setPeriod] = useState<Period>("14");
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  const load = useCallback((onCancelled: () => boolean) => {
    setState({ kind: "loading" });
    const days = period;
    Promise.all([
      apiGet<CampaignsResponse>("/api/campaigns"),
      apiGet<Overview>(`/api/overview?campaign=${encodeURIComponent(campaign)}&days=${days}`),
      apiGet<ProductsResponse>(`/api/products?campaign=${encodeURIComponent(campaign)}&days=${days}`),
    ])
      .then(([campaignsRes, overview, productsRes]) => {
        if (onCancelled()) return;
        setState({
          kind: "ready",
          campaigns: campaignsRes.campaigns,
          budgetsAvailable: campaignsRes.budgets_available,
          overview,
          products: productsRes.products,
        });
      })
      .catch((err) => {
        if (onCancelled()) return;
        setState({
          kind: "error",
          message: err instanceof ApiError ? err.errors.join(", ") : "Панель не может связаться с сервером",
        });
      });
  }, [campaign, period]);

  useEffect(() => {
    let cancelled = false;
    load(() => cancelled);
    return () => { cancelled = true; };
  }, [load]);

  if (state.kind === "loading") {
    return <div style={{ padding: 48, textAlign: "center", color: "var(--ink-2)" }}>Загрузка…</div>;
  }

  if (state.kind === "error") {
    return (
      <div style={{ padding: 48, textAlign: "center" }}>
        <p style={{ color: "var(--crit)" }}>{state.message}</p>
        <button type="button" className="btn" onClick={() => load(() => false)}>Повторить</button>
      </div>
    );
  }

  const { campaigns, budgetsAvailable, overview, products } = state;
  const campaignName = (id: string) => campaigns.find((c) => c.id === id)?.name || id;
  const campaignLabel = (p: Product) => p.campaign_ids.map(campaignName).join(", ") || "без кампании";

  // Владение состоянием тоггла — здесь, а не в строке: счётчик «биддер ведёт
  // N из M» в шапке списка должен увидеть переключение сразу, без похода
  // за списком заново. Меняем оптимистично, откатываем при ошибке PUT.
  async function handleToggle(product: Product, next: boolean): Promise<string | null> {
    const setEnabled = (enabled: boolean) =>
      setState((s) => (s.kind !== "ready" ? s : {
        ...s,
        products: s.products.map((p) => (p.sku === product.sku ? { ...p, enabled } : p)),
      }));
    setEnabled(next);
    const campaignId = product.campaign_ids[0];
    try {
      // Шлём ТОЛЬКО enabled — окно и дни недели API сохранит сам из текущих
      // значений. Досылать их «для полноты» нельзя: сотрёт расписание товара.
      await apiSend("PUT", `/api/products/${campaignId}/${product.sku}/control`, { enabled: next });
      return null;
    } catch (err) {
      setEnabled(!next);
      return err instanceof ApiError ? err.errors.join(", ") : "Не удалось сохранить";
    }
  }

  const enabledCount = products.filter((p) => p.enabled).length;
  const hasMultiCampaignProduct = products.some((p) => p.campaign_ids.length > 1);
  // Не campaigns.length: тот список зависит от доступности кабинета Kaspi
  // (budgets_available), а не от того, что реально показано в списке ниже.
  // Считаем от фактической принадлежности товаров, иначе число M соврёт.
  const campaignCount = new Set(products.flatMap((p) => p.campaign_ids)).size;

  return (
    <div>
      <div className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="brand-dot" aria-hidden="true" />
            Автопилот ставок
            <small>обновлено {fmtTs(overview.last_snapshot_ts)}</small>
          </div>
          <select
            className="plain"
            aria-label="Кампания"
            value={campaign}
            onChange={(e) => setCampaign(e.target.value)}
          >
            <option value="all">Все кампании</option>
            {campaigns.map((c) => (
              <option key={c.id} value={c.id}>{c.name ? `${c.name} · ${c.id}` : c.id}</option>
            ))}
          </select>
          <Segmented
            ariaLabel="Период"
            value={period}
            onChange={setPeriod}
            options={[
              { value: "7", label: "7 дней" },
              { value: "14", label: "14 дней" },
              { value: "30", label: "30 дней" },
            ]}
          />
          <button type="button" className="linkish" onClick={onLogout}>Выйти</button>
        </div>
      </div>

      <div className="wrap">
        <div className="pagehead">
          <h1>Товары</h1>
          <p className="sub">
            {products.length} товаров в {campaignCount} кампаниях · за последние <b>{overview.days} дней</b>
          </p>
        </div>

        <KpiBand
          overview={overview}
          campaign={campaign}
          campaigns={campaigns}
          budgetsAvailable={budgetsAvailable}
          hasMultiCampaignProduct={hasMultiCampaignProduct}
        />

        <div className="sec-head">
          <h2>Все товары</h2>
          <span className="count">биддер ведёт {enabledCount} из {products.length}</span>
        </div>

        <div className="group">
          {products.length > 0 && (
            <div className="cols" aria-hidden="true">
              <span>Товар</span>
              <span>Ставка {overview.days} дн</span>
              <span>Ставка</span>
              <span>TACoS</span>
              <span>CTR</span>
              <span>ROAS</span>
              <span>Бот</span>
            </div>
          )}
          {products.length === 0 ? (
            <p className="empty-list">Нет товаров с рекламой за выбранный период.</p>
          ) : (
            products.map((p) => (
              <ProductRow
                key={p.sku}
                product={p}
                campaignLabel={campaignLabel(p)}
                onToggle={(next) => handleToggle(p, next)}
              />
            ))
          )}
        </div>

        <p className="footnote">
          TACoS считается от реальной выручки Shop API за выбранный период. ROAS показан по ней же;
          число кабинета отличается, потому что не учитывает отмены заказов.
        </p>
      </div>
    </div>
  );
}
