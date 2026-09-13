import { useState } from "react";
import { fmtMoney, fmtPct, fmtX, tacosHealth } from "../../api/format";
import Sparkline from "../../components/Sparkline";
import Switch from "../../components/Switch";
import Tag from "../../components/Tag";
import ProductPanel from "./ProductPanel";
import type { Product } from "./types";

type Props = {
  product: Product;
  /** Имена кампаний товара — уже разрешённые по campaign_ids, готовая строка
   *  для подписи под названием («код · Кампания 1, Кампания 2»). */
  campaignLabel: string;
  /** Состояние тоггла и его отправка — во владении экрана (ProductsScreen),
   *  не строки: счётчик «биддер ведёт N из M» в шапке списка обязан
   *  увидеть переключение сразу же, а не после перезагрузки списка.
   *  Возвращает текст ошибки (из ApiError.errors) при неудаче, иначе null —
   *  строка сама откатывать состояние не должна, это тоже забота экрана. */
  onToggle: (next: boolean) => Promise<string | null>;
};

/** Строка списка товаров (.row, docs/design/autopilot-mockup.html): название
 *  и код, спарклайн ставки, ставка, TACoS тегом, CTR, ROAS, тоггл.
 *
 *  Клик по строке раскрывает панель — в этой задаче заглушка, наполняется
 *  в задаче 3. Тоггл гасит всплытие клика: иначе переключение биддера
 *  попутно раскрывало бы/закрывало строку. */
export default function ProductRow({ product, campaignLabel, onToggle }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleExpanded = () => setExpanded((v) => !v);

  function onRowKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggleExpanded();
    }
  }

  async function handleToggle(next: boolean) {
    setError(null);
    setBusy(true);
    const message = await onToggle(next);   // экран уже применил/откатил enabled
    if (message) setError(message);
    setBusy(false);
  }

  return (
    <>
      <div
        className="row"
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={toggleExpanded}
        onKeyDown={onRowKeyDown}
      >
        <span className="pname">
          <span className="chev" aria-hidden="true">▶</span>
          <span className="pname-txt">
            <strong>{product.name ?? "Без названия"}</strong>
            <span>{product.merchant_sku} · {campaignLabel}</span>
          </span>
        </span>
        <span className="hide-sm">
          <Sparkline values={product.bid_spark} />
        </span>
        <span className="num hide-sm">
          {fmtMoney(product.bid)} <span className="unit">₸</span>
        </span>
        <span className="num">
          <Tag health={tacosHealth(product.tacos)}>{fmtPct(product.tacos)}</Tag>
        </span>
        <span className="num dim hide-sm">{fmtPct(product.ctr)}</span>
        <span className="num dim hide-sm">{fmtX(product.roas)}</span>
        <span onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
          <Switch
            checked={product.enabled}
            onChange={handleToggle}
            disabled={busy || product.campaign_ids.length === 0}
            label={
              product.enabled
                ? `Биддер ведёт «${product.name ?? product.sku}»`
                : `Биддер выключен для «${product.name ?? product.sku}»`
            }
          />
        </span>
      </div>
      {error && (
        <div role="alert" style={{ padding: "4px 18px 8px", fontSize: 12, color: "var(--crit)" }}>
          {error}
        </div>
      )}
      {expanded && (
        <div className="detail">
          {/* Первая кампания товара — тот же выбор, что и в тоггле выше:
             sku-настройки едины, а control/decisions per-кампания. */}
          <ProductPanel campaignId={product.campaign_ids[0] ?? ""} sku={product.sku} />
        </div>
      )}
    </>
  );
}
