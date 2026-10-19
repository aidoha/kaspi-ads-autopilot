import { useState } from "react";
import { apiSend, ApiError } from "../../api/client";
import { fmtMoney, fmtPct } from "../../api/format";
import { breakdown, parseMoney, parsePercent } from "./reportMath";
import type { ReportProduct } from "./types";

type Props = {
  product: ReportProduct;
  /** После сохранения себестоимости экран перезапрашивает сводку: прибыль
   *  считает сервер, локально её не пересчитываем. */
  onSaved: () => void;
};

const n = (v: number) => Math.round(v).toLocaleString("ru-RU").replace(/\s/g, " ");

/** Строка товара в отчёте + раскрывающаяся панель: куда уходит выручка,
 *  воронка рекламы, заказы и поля себестоимости/комиссии. */
export default function ReportRow({ product: p, onSaved }: Props) {
  const [open, setOpen] = useState(false);

  const profitCls = p.profit === null ? "num dim" : p.profit < 0 ? "num neg" : "num";
  const sub = [p.merchant_sku ?? "нет в архиве заказов", ...p.kaspi_skus.map((s) => `Kaspi ${s}`)]
    .join(" · ");

  return (
    <>
      <div
        className="row rrow"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen((v) => !v); }
        }}
      >
        <span className="pname">
          <span className="chev" aria-hidden="true">▶</span>
          <span className="pname-txt">
            <strong>{p.name || "Без названия"}</strong>
            <span>{sub}</span>
          </span>
        </span>
        <span className="num" data-label="Выручка">{fmtMoney(p.revenue)}</span>
        <span className="num" data-label="Заказы">{p.orders}</span>
        <span className="num" data-label="Реклама">
          {p.has_ads ? fmtMoney(p.ad_cost) : <span className="dim">—</span>}
        </span>
        <span className="num" data-label="Бонусы">
          {p.has_bonus ? fmtMoney(p.bonus) : <span className="dim">—</span>}
        </span>
        <span className={profitCls} data-label="Прибыль">
          {p.profit === null ? <span className="need-cost">нужна себест.</span> : fmtMoney(p.profit)}
        </span>
        <span className="num dim" data-label="Маржа">{fmtPct(p.margin)}</span>
      </div>
      {open && <Detail p={p} onSaved={onSaved} />}
    </>
  );
}

function Detail({ p, onSaved }: { p: ReportProduct; onSaved: () => void }) {
  const segs = breakdown(p);
  const lossNote = p.profit !== null && p.profit < 0;

  return (
    <div className="detail rdetail">
      <div className="rd-grid">
        <section>
          <h3 className="rd-h">Куда уходит выручка</h3>
          {segs.length > 0 ? (
            <>
              <div className="stack" role="img"
                   aria-label={segs.map((s) => `${s.label} ${n(s.value)} ₸`).join(", ")}>
                {segs.map((s) => (
                  <span key={s.key} className={`seg seg-${s.key}`} style={{ flexGrow: s.share }}
                        title={`${s.label}: ${n(s.value)} ₸`} />
                ))}
              </div>
              {lossNote && <p className="rd-note">Расходы больше выручки — полоса показывает расходы.</p>}
            </>
          ) : <p className="rd-note">За период нет ни продаж, ни расходов.</p>}
          <dl className="ledger">
            <div className="lg-row lg-head"><dt>Выручка</dt><dd>{fmtMoney(p.revenue)} ₸</dd></div>
            <Line k="cogs" label="Себестоимость" v={p.cogs} rev={p.revenue}
                  hint={p.unit_cost === null && p.units > 0 ? "не задана" : `${n(p.units)} шт × ${fmtMoney(p.unit_cost)}`} />
            <Line k="commission" label="Комиссия Kaspi" v={p.commission} rev={p.revenue}
                  hint={p.commission_pct === null ? "не задана"
                    : `${fmtPct(p.commission_pct)}${p.commission_source === "default" ? " по умолчанию" : ""}`} />
            <Line k="delivery" label="Доставка" v={p.delivery} rev={p.revenue} />
            <Line k="ads" label="Реклама" v={p.ad_cost} rev={p.revenue} />
            <Line k="bonus" label="Бонусы клиентам" v={p.bonus} rev={p.revenue} />
            <div className="lg-row lg-total">
              <dt>Прибыль</dt>
              <dd className={p.profit !== null && p.profit < 0 ? "neg" : ""}>
                {p.profit === null ? "—" : `${fmtMoney(p.profit)} ₸`}
                {p.margin !== null && <small> · {fmtPct(p.margin)}</small>}
              </dd>
            </div>
          </dl>
        </section>

        <section>
          <h3 className="rd-h">Заказы</h3>
          <dl className="facts">
            <Fact label="Заказов" v={n(p.orders)} />
            <Fact label="Штук" v={n(p.units)} />
            <Fact label="Средний чек" v={p.avg_check === null ? "—" : `${fmtMoney(p.avg_check)} ₸`} />
            <Fact label="Отмены / возвраты" v={`${p.cancelled} / ${p.returned}`} />
          </dl>
          <h3 className="rd-h">Реклама</h3>
          {p.has_ads ? (
            <dl className="facts">
              <Fact label="Показы → клики" v={`${n(p.ad_views)} → ${n(p.ad_clicks)}`} />
              <Fact label="CTR / цена клика" v={`${fmtPct(p.ctr)} / ${p.cpc === null ? "—" : `${fmtMoney(p.cpc)} ₸`}`} />
              <Fact label="Заказы с рекламы" v={`${p.ad_orders} на ${fmtMoney(p.ad_revenue)} ₸`} />
              <Fact label="ДРР / TACoS" v={`${fmtPct(p.drr)} / ${fmtPct(p.tacos)}`} />
            </dl>
          ) : <p className="rd-note">Отчётов рекламы по товару за период нет.</p>}
          {p.has_bonus && (
            <>
              <h3 className="rd-h">Бонусная акция</h3>
              <dl className="facts">
                <Fact label="Выплачено бонусов" v={`${fmtMoney(p.bonus)} ₸`} />
                <Fact label="Заказы по акции" v={`${p.bonus_orders} на ${fmtMoney(p.bonus_revenue)} ₸`} />
              </dl>
            </>
          )}
        </section>

        <section>
          <h3 className="rd-h">Себестоимость и комиссия</h3>
          {p.merchant_sku
            ? <CostForm p={p} onSaved={onSaved} />
            : <p className="rd-note">Товара нет в архиве заказов — продаж за период не было,
                себестоимость ни на что не влияет.</p>}
        </section>
      </div>
    </div>
  );
}

function Line({ k, label, v, rev, hint }:
  { k: string; label: string; v: number | null; rev: number; hint?: string }) {
  return (
    <div className="lg-row">
      <dt><span className={`dot seg-${k}`} aria-hidden="true" />{label}
        {hint && <small> · {hint}</small>}</dt>
      <dd>
        {v === null ? "—" : v === 0 ? "0 ₸" : `−${fmtMoney(v)} ₸`}
        {v !== null && rev > 0 && <small> {fmtPct(v / rev)}</small>}
      </dd>
    </div>
  );
}

function Fact({ label, v }: { label: string; v: string }) {
  return <div className="fact"><dt>{label}</dt><dd>{v}</dd></div>;
}

function CostForm({ p, onSaved }: { p: ReportProduct; onSaved: () => void }) {
  const ownComm = p.commission_source === "product" ? p.commission_pct : null;
  const [cost, setCost] = useState(p.unit_cost === null ? "" : String(p.unit_cost));
  const [comm, setComm] = useState(ownComm === null ? "" : String(+(ownComm * 100).toFixed(2)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const c = parseMoney(cost), k = parsePercent(comm);
    if (c === "bad") return setError("Себестоимость — число от 0");
    if (k === "bad") return setError("Комиссия — процент от 0 до 90");
    setBusy(true);
    setError(null);
    try {
      await apiSend("PUT", `/api/reports/costs/${encodeURIComponent(p.merchant_sku!)}`,
        { unit_cost: c, commission_pct: k });
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.errors.join(", ") : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="cost-form" onSubmit={save}>
      <label>
        <span>Закупка за штуку, ₸</span>
        <input inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)}
               placeholder="напр. 6500" />
      </label>
      <label>
        <span>Комиссия Kaspi, %</span>
        <input inputMode="decimal" value={comm} onChange={(e) => setComm(e.target.value)}
               placeholder={p.commission_source === "default" && p.commission_pct !== null
                 ? `${+(p.commission_pct * 100).toFixed(2)} по умолч.` : "напр. 12"} />
      </label>
      <button type="submit" className="btn btn-primary" disabled={busy}>
        {busy ? "Сохраняем…" : "Сохранить"}
      </button>
      {error && <p className="form-err">{error}</p>}
      <p className="rd-note">Пустая комиссия — берётся общая по умолчанию.</p>
    </form>
  );
}
