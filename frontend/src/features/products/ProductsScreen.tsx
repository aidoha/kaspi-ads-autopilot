import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend, ApiError } from "../../api/client";
import { fmtTs } from "../../api/format";
import Segmented from "../../components/Segmented";
import "../../styles/products.css";
import KpiBand from "./KpiBand";
import ProductRow from "./ProductRow";
import { splitByVisibility } from "./visibility";
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
  /** Тестовый режим — общее состояние приложения (App), не локальное: та же
   *  величина, что тоггл на экране настроек. null — ещё не узнали. */
  dryRun: boolean | null;
  onOpenSettings: () => void;
};

/** Главный экран: шапка с фильтрами, строка KPI, список товаров с
 *  тогглерами. Разметка и стили — из утверждённого макета
 *  docs/design/autopilot-mockup.html. */
export default function ProductsScreen({ onLogout, dryRun, onOpenSettings }: Props) {
  const [campaign, setCampaign] = useState("all");
  const [period, setPeriod] = useState<Period>("14");
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [showHidden, setShowHidden] = useState(false);

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
    try {
      // Товар может вестись в нескольких кампаниях одновременно (см.
      // campaign_ids) — сервер считает enabled как any(...) по ним всем,
      // поэтому тоггл обязан выключить биддера ВО ВСЕХ, а не только в
      // первой. Иначе биддер продолжит управлять ставкой во второй
      // кампании, а владелец будет считать товар остановленным.
      //
      // Шлём ТОЛЬКО enabled — окно и дни недели API сохранит сам из текущих
      // значений. Досылать их «для полноты» нельзя: сотрёт расписание товара.
      await Promise.all(
        product.campaign_ids.map((campaignId) =>
          apiSend("PUT", `/api/products/${campaignId}/${product.sku}/control`, { enabled: next })),
      );
      return null;
    } catch (err) {
      setEnabled(!next);
      return err instanceof ApiError ? err.errors.join(", ") : "Не удалось сохранить";
    }
  }

  // Скрытие — только вид: биддер к нему не прикасается, поэтому идёт
  // отдельным PUT и ничего не знает про campaign_ids. Оптимистично, как и
  // тоггл: строка обязана уехать в другой список сразу, а не после ответа.
  async function handleHide(product: Product, next: boolean): Promise<string | null> {
    const setHidden = (hidden: boolean) =>
      setState((s) => (s.kind !== "ready" ? s : {
        ...s,
        products: s.products.map((p) => (p.sku === product.sku ? { ...p, hidden } : p)),
      }));
    setHidden(next);
    try {
      await apiSend("PUT", `/api/products/${product.sku}/visibility`, { hidden: next });
      return null;
    } catch (err) {
      setHidden(!next);
      return err instanceof ApiError ? err.errors.join(", ") : "Не удалось сохранить";
    }
  }

  // «Обновить сейчас» — POST /api/refresh идёт в кабинет Kaspi и Shop API
  // синхронно, поэтому по успеху перезапрашиваем обзор и список заново, а
  // не патчим их локально — сервер знает, что реально изменилось.
  async function refreshNow() {
    setRefreshing(true);
    setRefreshError(null);
    try {
      await apiSend("POST", "/api/refresh");
      load(() => false);
    } catch (err) {
      setRefreshError(err instanceof ApiError ? err.errors.join(", ") : "Не удалось обновить данные");
    } finally {
      setRefreshing(false);
    }
  }

  // Счётчики — по ВИДИМЫМ товарам (см. visibility.ts). Не campaigns.length:
  // тот список зависит от доступности кабинета Kaspi (budgets_available), а
  // не от того, что реально показано в списке ниже.
  const { visible, hidden, enabledVisible, enabledHidden, campaignCount } =
    splitByVisibility(products);
  const hasMultiCampaignProduct = products.some((p) => p.campaign_ids.length > 1);

  const row = (p: Product) => (
    <ProductRow
      key={p.sku}
      product={p}
      campaignLabel={campaignLabel(p)}
      onToggle={(next) => handleToggle(p, next)}
      onHide={(next) => handleHide(p, next)}
    />
  );

  return (
    <div>
      <div className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="brand-dot" aria-hidden="true" />
            Автопилот ставок
            <small>обновлено {fmtTs(overview.last_snapshot_ts)}</small>
            <button type="button" className="linkish" onClick={refreshNow} disabled={refreshing}>
              {refreshing ? "Обновляем…" : "Обновить сейчас"}
            </button>
            {refreshError && <small style={{ color: "var(--crit)" }}>{refreshError}</small>}
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
          {dryRun && <span className="pill-run">Тестовый режим</span>}
          <button type="button" className="linkish" onClick={onLogout}>Выйти</button>
        </div>
      </div>

      <div className="wrap">
        <div className="pagehead">
          <h1>Товары</h1>
          <p className="sub">
            {visible.length} товаров в {campaignCount} кампаниях · за последние <b>{overview.days} дней</b>
            {dryRun && <> · ставки в кабинет не уходят — включён тестовый режим</>}
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
          <span className="count">биддер ведёт {enabledVisible} из {visible.length}</span>
          <button type="button" className="linkish" onClick={onOpenSettings}>Настройки биддера</button>
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
          {products.length === 0 && (
            <p className="empty-list">Нет товаров с рекламой за выбранный период.</p>
          )}
          {products.length > 0 && visible.length === 0 && (
            <p className="empty-list">Все товары скрыты — раскройте раздел ниже, чтобы вернуть.</p>
          )}
          {visible.map(row)}

          {/* Скрытое не удалено и не забыто: раздел всегда на виду внизу
             списка, и вернуть товар можно оттуда же, где его спрятали.
             Внутри — те же строки: раскрытие, графики и тоггл биддера у
             скрытого товара работают как у любого другого. */}
          {hidden.length > 0 && (
            <button
              type="button"
              className="hidden-head"
              aria-expanded={showHidden}
              onClick={() => setShowHidden((v) => !v)}
            >
              <span className="chev" aria-hidden="true">▶</span>
              Скрытые · {hidden.length}
              {enabledHidden > 0 && (
                <span className="hidden-warn">биддер ведёт {enabledHidden}</span>
              )}
            </button>
          )}
          {showHidden && hidden.map(row)}
        </div>

        <p className="footnote">
          TACoS считается от реальной выручки Shop API за выбранный период. ROAS показан по ней же;
          число кабинета отличается, потому что не учитывает отмены заказов.
        </p>
      </div>
    </div>
  );
}
