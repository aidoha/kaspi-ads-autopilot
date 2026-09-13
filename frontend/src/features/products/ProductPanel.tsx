import { useCallback, useEffect, useState } from "react";
import { apiGet, ApiError } from "../../api/client";
import Segmented from "../../components/Segmented";
import ChartsTab from "./ChartsTab";
import DecisionsTab from "./DecisionsTab";
import SettingsTab from "./SettingsTab";
import type { ProductDetail } from "./types";

type Tab = "charts" | "settings" | "dec";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; detail: ProductDetail };

type Props = {
  /** Кампания, для которой показываем расписание/решения товара — первая
   *  из campaign_ids (тот же выбор, что уже делает ProductsScreen для
   *  тоггла «биддер ведёт»): sku-настройки едины для товара, а control и
   *  decisions — per-кампания, нужна какая-то одна для карточки. */
  campaignId: string;
  sku: string;
};

/** Панель товара (.detail-top + .tabpanel из макета): грузит карточку по
 *  раскрытию строки, переключает вкладки «Графики / Настройки / Решения». */
export default function ProductPanel({ campaignId, sku }: Props) {
  const [tab, setTab] = useState<Tab>("charts");
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback((onCancelled: () => boolean) => {
    setState({ kind: "loading" });
    apiGet<ProductDetail>(`/api/products/${campaignId}/${sku}`)
      .then((detail) => { if (!onCancelled()) setState({ kind: "ready", detail }); })
      .catch((err) => {
        if (onCancelled()) return;
        setState({
          kind: "error",
          message: err instanceof ApiError ? err.errors.join(", ") : "Не удалось загрузить панель товара",
        });
      });
  }, [campaignId, sku]);

  useEffect(() => {
    let cancelled = false;
    load(() => cancelled);
    return () => { cancelled = true; };
  }, [load]);

  if (!campaignId) {
    return <p className="empty-list">У товара нет кампании — панель недоступна.</p>;
  }

  if (state.kind === "loading") {
    return <p className="empty-list">Загрузка панели…</p>;
  }

  if (state.kind === "error") {
    return (
      <div>
        <p style={{ color: "var(--crit)" }}>{state.message}</p>
        <button type="button" className="btn" onClick={() => load(() => false)}>Повторить</button>
      </div>
    );
  }

  const { detail } = state;

  return (
    <div>
      <div className="detail-top">
        <Segmented
          ariaLabel="Вкладка панели товара"
          value={tab}
          onChange={setTab}
          options={[
            { value: "charts", label: "Графики" },
            { value: "settings", label: "Настройки" },
            { value: "dec", label: "Решения" },
          ]}
        />
        <span className="ai-soon">
          <button className="btn btn-primary" type="button" disabled
                  title="Появится позже — эндпоинтов разбора с ИИ ещё нет">
            Разобрать с ИИ
          </button>
          <span className="ai-soon-note">появится позже</span>
        </span>
      </div>

      <div className="tabpanel" hidden={tab !== "charts"}>
        <ChartsTab campaignId={campaignId} sku={sku} />
      </div>

      <div className="tabpanel" hidden={tab !== "settings"}>
        <SettingsTab
          campaignId={campaignId}
          sku={sku}
          detail={detail}
          onSaved={() => load(() => false)}
        />
      </div>

      <div className="tabpanel" hidden={tab !== "dec"}>
        <DecisionsTab decisions={detail.decisions} />
      </div>
    </div>
  );
}
