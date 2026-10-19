import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiSend, ApiError } from "../../api/client";
import { fmtMoney, fmtPct, fmtTs } from "../../api/format";
import "../../styles/products.css";
import "../../styles/reports.css";
import ReportRow from "./ReportRow";
import { delta, parsePercent, periodLabel, sortProducts, type SortKey } from "./reportMath";
import type { Dashboard, Upload, UploadResult } from "./types";

type Props = { onBack: () => void; onLogout: () => void };

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: Dashboard };

type FileLog = { name: string; ok: boolean; text: string };

const errText = (err: unknown, fallback: string) =>
  err instanceof ApiError ? err.errors.join(", ") : fallback;

/** Экран «Деньги по товарам»: владелец раз в неделю загружает выгрузки
 *  кабинета Kaspi (реклама, бонусы, архив заказов) и видит по каждому
 *  товару выручку, заказы, куда уходят деньги и что остаётся. Всё считает
 *  сервер (core/reports.py), здесь — только показ. */
export default function ReportsScreen({ onBack, onLogout }: Props) {
  const [period, setPeriod] = useState<string>("");   // "start|end", "" — свежий
  const [state, setState] = useState<Load>({ kind: "loading" });
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "revenue", desc: true });

  const load = useCallback((quiet = false) => {
    if (!quiet) setState({ kind: "loading" });
    const [s, e] = period.split("|");
    const q = period ? `?start=${s}&end=${e}` : "";
    apiGet<Dashboard>(`/api/reports/dashboard${q}`)
      .then((data) => setState({ kind: "ready", data }))
      .catch((err) => setState({ kind: "error", message: errText(err, "Панель не может связаться с сервером") }));
  }, [period]);

  useEffect(() => { load(); }, [load]);

  const cur = state.kind === "ready" && !state.data.empty ? state.data : null;
  const topbar = (
    <div className="topbar">
      <div className="topbar-inner">
        <button type="button" className="linkish" onClick={onBack}>← Товары</button>
        <div className="brand">Деньги по товарам</div>
        {cur && cur.periods.length > 0 && (
          <div className="topbar-filters">
          <select className="plain" aria-label="Период" value={period || `${cur.start}|${cur.end}`}
                  onChange={(e) => setPeriod(e.target.value)}>
            {!cur.periods.some((p) => p.start === cur.start && p.end === cur.end) && (
              <option value={`${cur.start}|${cur.end}`}>{periodLabel(cur.start, cur.end)}</option>
            )}
            {cur.periods.map((p) => (
              <option key={`${p.start}|${p.end}`} value={`${p.start}|${p.end}`}>
                {periodLabel(p.start, p.end)} · {p.files} {p.files === 1 ? "отчёт" : "отчёта"}
              </option>
            ))}
          </select>
          </div>
        )}
        <button type="button" className="linkish" onClick={onLogout}>Выйти</button>
      </div>
    </div>
  );

  if (state.kind !== "ready") {
    return (
      <div>
        {topbar}
        <div style={{ padding: 48, textAlign: "center" }}>
          {state.kind === "loading"
            ? <span style={{ color: "var(--ink-2)" }}>Загрузка…</span>
            : <>
                <p style={{ color: "var(--crit)" }}>{state.message}</p>
                <button type="button" className="btn" onClick={() => load()}>Повторить</button>
              </>}
        </div>
      </div>
    );
  }

  const d = state.data;
  // После загрузки файлов — на свежий период: новые отчёты почти всегда за
  // последнюю неделю, и показывать старую было бы странно.
  const afterUpload = () => { setPeriod(""); load(true); };

  if (d.empty) {
    return (
      <div>
        {topbar}
        <div className="wrap">
          <div className="pagehead">
            <h1>Деньги по товарам</h1>
            <p className="sub">Загрузите выгрузки из кабинета Kaspi — и здесь появится выручка, расходы и прибыль по каждому товару.</p>
          </div>
          <UploadBox onDone={afterUpload} />
          <HowTo />
        </div>
      </div>
    );
  }

  const t = d.totals, pt = d.prev.totals;
  const products = sortProducts(d.products, sort.key, sort.desc);
  const needCost = d.products.filter((p) => p.needs_cost).length;
  const sortBtn = (key: SortKey, label: string, cls = "") => (
    <button type="button" className={`colbtn ${cls}`} onClick={() =>
      setSort((s) => ({ key, desc: s.key === key ? !s.desc : key !== "name" }))}
      aria-sort={sort.key === key ? (sort.desc ? "descending" : "ascending") : undefined}>
      {label}{sort.key === key ? (sort.desc ? " ↓" : " ↑") : ""}
    </button>
  );

  return (
    <div>
      {topbar}
      <div className="wrap">
        <div className="pagehead">
          <h1>Деньги по товарам</h1>
          <p className="sub">
            <b>{periodLabel(d.start, d.end)}</b> · {d.products.length} товаров · сравнение с{" "}
            {periodLabel(d.prev.start, d.prev.end)}
          </p>
        </div>

        <div className="kpis">
          <Kpi label="Выручка" v={t.revenue} prev={pt.revenue} money
               note={`${t.cancelled} отмен · ${t.returned} возвратов`} />
          <Kpi label="Заказы" v={t.orders} prev={pt.orders} note={`${Math.round(t.units)} шт · с рекламы ${t.ad_orders}`} />
          <Kpi label="Реклама" v={t.ad_cost} prev={pt.ad_cost} money invert
               note={d.has_ads ? `TACoS ${fmtPct(t.tacos)}` : "отчёт рекламы не загружен"} />
          <Kpi label="Бонусы клиентам" v={t.bonus} prev={pt.bonus} money invert
               note={d.has_bonus ? `доставка ${fmtMoney(t.delivery)} ₸` : "бонусных отчётов нет"} />
          {/* Сумма прибыли по части товаров выглядела бы как прибыль
             магазина — поэтому итог только когда себестоимость есть у всех,
             а частичный — мелко и с оговоркой. */}
          <Kpi label="Прибыль" v={t.profit_missing ? null : t.profit}
               prev={pt.profit_missing ? null : pt.profit} money
               note={t.profit_missing
                 ? `нет себестоимости у ${t.profit_missing} из ${d.products.length}`
                   + (t.profit !== null ? ` · по остальным ${fmtMoney(t.profit)} ₸` : "")
                 : `маржа ${fmtPct(t.margin)}`} />
        </div>

        {d.warnings.length > 0 && (
          <div className="warns">{d.warnings.map((w) => <p key={w}>{w}</p>)}</div>
        )}

        <div className="sec-head">
          <h2>Товары</h2>
          <span className="count">
            {needCost > 0 ? `у ${needCost} нет себестоимости — раскройте строку и впишите` : "по всем товарам есть себестоимость"}
          </span>
          <label className="sort-sm">Сортировка
            <select className="plain" value={sort.key}
                    onChange={(e) => setSort({ key: e.target.value as SortKey, desc: e.target.value !== "name" })}>
              <option value="revenue">выручка</option>
              <option value="profit">прибыль</option>
              <option value="margin">маржа</option>
              <option value="orders">заказы</option>
              <option value="ad_cost">реклама</option>
              <option value="bonus">бонусы</option>
              <option value="name">название</option>
            </select>
          </label>
          <DefaultCommission value={d.default_commission_pct} onSaved={() => load(true)} />
        </div>

        <div className="group">
          <div className="cols rcols">
            {sortBtn("name", "Товар")}
            {sortBtn("revenue", "Выручка")}
            {sortBtn("orders", "Заказы")}
            {sortBtn("ad_cost", "Реклама")}
            {sortBtn("bonus", "Бонусы")}
            {sortBtn("profit", "Прибыль")}
            {sortBtn("margin", "Маржа")}
          </div>
          {products.length === 0 && <p className="empty-list">За этот период нет ни заказов, ни расходов.</p>}
          {products.map((p) => <ReportRow key={p.key} product={p} onSaved={() => load(true)} />)}
        </div>
        <p className="footnote">
          Выручка и заказы — из архива заказов, без отмен и возвратов. Реклама и бонусы — из отчётов
          кабинета, чьи даты лежат внутри периода. Доставка — «стоимость доставки для продавца» из
          архива. Прибыль = выручка − себестоимость − комиссия − доставка − реклама − бонусы.
        </p>

        {d.campaigns.length > 0 && (
          <>
            <div className="sec-head"><h2>Бонусные кампании</h2></div>
            <div className="group">
              <div className="cols ccols" aria-hidden="true">
                <span>Кампания</span><span>Клики</span><span>Заказы</span><span>Сумма заказов</span><span>Бонусы</span>
              </div>
              {d.campaigns.map((c, i) => (
                <div className="row ccol-row" key={`${c.name}-${c.start_date}-${i}`}>
                  <span className="pname-txt"><strong>{c.name}</strong><span>с {c.start_date} · {c.status}</span></span>
                  <span className="num">{fmtMoney(c.clicks)}</span>
                  <span className="num">{c.orders}</span>
                  <span className="num">{fmtMoney(c.revenue)}</span>
                  <span className="num">{fmtMoney(c.bonus)}</span>
                </div>
              ))}
            </div>
          </>
        )}

        <div className="sec-head"><h2>Загрузить отчёты</h2></div>
        <UploadBox onDone={afterUpload} />
        <UploadList uploads={d.uploads} onChanged={() => load(true)} />
        <HowTo />
      </div>
    </div>
  );
}

function Kpi({ label, v, prev, note, money, invert }: {
  label: string; v: number | null; prev: number | null; note: string; money?: boolean; invert?: boolean;
}) {
  const dl = delta(v, prev);
  // Для расходов рост — плохо: цвет стрелки следует смыслу, а не знаку.
  const good = dl && dl.dir !== "flat" && ((dl.dir === "up") !== Boolean(invert));
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className={`kpi-val${v !== null && v < 0 ? " neg" : ""}`}>
        {money && <span className="cur">₸ </span>}{v === null ? "—" : fmtMoney(v)}
      </div>
      <div className="kpi-note">
        {dl && (
          <span className={dl.dir === "flat" ? "" : good ? "up" : "down"}>
            {dl.dir === "up" ? "▲" : dl.dir === "down" ? "▼" : "="} {Math.abs(Math.round(dl.pct * 100))}%{" "}
          </span>
        )}
        {note}
      </div>
    </div>
  );
}

function DefaultCommission({ value, onSaved }: { value: number | null; onSaved: () => void }) {
  const [edit, setEdit] = useState(false);
  const [text, setText] = useState(value === null ? "" : String(+(value * 100).toFixed(2)));
  const [error, setError] = useState<string | null>(null);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const k = parsePercent(text);
    if (k === "bad") return setError("от 0 до 90");
    try {
      await apiSend("PUT", "/api/reports/costs/*", { commission_pct: k });
      setEdit(false);
      setError(null);
      onSaved();
    } catch (err) {
      setError(errText(err, "не сохранилось"));
    }
  }

  if (!edit) {
    return (
      <button type="button" className="linkish" onClick={() => setEdit(true)}>
        Комиссия Kaspi по умолчанию: {value === null ? "не задана" : fmtPct(value)}
      </button>
    );
  }
  return (
    <form className="inline-form" onSubmit={save}>
      <label>Комиссия по умолчанию, %{" "}
        <input autoFocus inputMode="decimal" value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <button type="submit" className="btn btn-primary">OK</button>
      <button type="button" className="linkish" onClick={() => setEdit(false)}>Отмена</button>
      {error && <span className="form-err">{error}</span>}
    </form>
  );
}

function UploadBox({ onDone }: { onDone: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<FileLog[]>([]);

  async function send(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    setBusy(true);
    const out: FileLog[] = [];
    // По одному, а не параллельно: архив заказов должен лечь раньше, чем
    // по нему начнут склеивать товары, а SQLite всё равно пишет по очереди.
    const ordered = [...list].sort((a, b) => Number(b.name.endsWith(".xlsx")) - Number(a.name.endsWith(".xlsx")));
    for (const f of ordered) {
      try {
        const r = await apiSend<UploadResult>("POST",
          `/api/reports/upload?filename=${encodeURIComponent(f.name)}`, f);
        const when = r.period_start && r.period_end ? ` · ${periodLabel(r.period_start, r.period_end)}` : "";
        out.push({ name: f.name, ok: true,
                   text: r.duplicate ? "уже был загружен — пропущен" : `${r.kind_label}${when} · ${r.rows} строк` });
      } catch (err) {
        out.push({ name: f.name, ok: false, text: errText(err, "не загрузился") });
      }
      setLog([...out]);
    }
    setBusy(false);
    if (out.some((x) => x.ok)) onDone();
  }

  return (
    <div>
      <div
        className={`drop${drag ? " drag" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); send(e.dataTransfer.files); }}
      >
        <p><b>Перетащите файлы сюда</b> — можно все сразу</p>
        <p className="rd-note">CSV-отчёты из кабинета Kaspi (реклама, бонусы, кампании) и архив заказов .xlsx</p>
        <button type="button" className="btn" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? "Загружаем…" : "Выбрать файлы"}
        </button>
        <input ref={input} type="file" multiple accept=".csv,.xlsx" hidden
               onChange={(e) => { if (e.target.files) send(e.target.files); e.target.value = ""; }} />
      </div>
      {log.length > 0 && (
        <ul className="up-log">
          {log.map((l, i) => (
            <li key={i} className={l.ok ? "" : "bad"}><b>{l.ok ? "✓" : "✕"} {l.name}</b> — {l.text}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function UploadList({ uploads, onChanged }: { uploads: Upload[]; onChanged: () => void }) {
  const [confirm, setConfirm] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!uploads.length) return null;

  async function remove(id: number) {
    try {
      await apiSend("DELETE", `/api/reports/uploads/${id}`);
      setConfirm(null);
      onChanged();
    } catch (err) {
      setError(errText(err, "Не удалось удалить"));
    }
  }

  return (
    <div className="group uploads">
      {uploads.map((u) => (
        <div className="up-row" key={u.id}>
          <span className="pname-txt">
            <strong>{u.filename}</strong>
            <span>
              {u.kind_label}
              {u.period_start && u.period_end && u.kind !== "orders" && ` · ${periodLabel(u.period_start, u.period_end)}`}
              {u.kind === "orders" && u.period_start && u.period_end && ` · заказы ${u.period_start} – ${u.period_end}`}
              {` · ${u.rows} строк · ${fmtTs(u.ts)}`}
            </span>
          </span>
          {confirm === u.id ? (
            <span className="up-actions">
              <button type="button" className="linkish danger" onClick={() => remove(u.id)}>Удалить</button>
              <button type="button" className="linkish" onClick={() => setConfirm(null)}>Отмена</button>
            </span>
          ) : (
            <button type="button" className="linkish" onClick={() => setConfirm(u.id)}>Удалить…</button>
          )}
        </div>
      ))}
      {error && <p className="form-err" style={{ padding: "0 18px 12px" }}>{error}</p>}
    </div>
  );
}

function HowTo() {
  return (
    <details className="howto">
      <summary>Что и откуда выгружать каждую неделю</summary>
      <ol>
        <li><b>Архив заказов</b> — кабинет продавца → Заказы → Архив → выгрузить в Excel. Даёт выручку,
          заказы, отмены и доставку. Каждый раз можно грузить весь архив целиком — старые заказы обновятся, не задвоятся.</li>
        <li><b>Реклама</b> — Kaspi Реклама → кампания → «Отчёт по товарам» за неделю. По файлу на каждую кампанию.</li>
        <li><b>Бонусы</b> — бонусная кампания → «Отчёт по товарам» и общий «Отчёт по кампаниям» за ту же неделю.</li>
        <li>Не переименовывайте CSV: период недели берётся из имени файла.</li>
      </ol>
    </details>
  );
}

