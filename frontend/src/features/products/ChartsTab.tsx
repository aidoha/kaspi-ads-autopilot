import { useCallback, useEffect, useState } from "react";
import { apiGet, ApiError } from "../../api/client";
import { fmtMoney, fmtPct, fmtTs } from "../../api/format";
import type { ChartMarker, ChartSeries } from "../../components/LineChart";
import LineChart from "../../components/LineChart";
import Segmented from "../../components/Segmented";
import type { ProductSeries } from "./types";

type Period = "7" | "14" | "30";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; series: ProductSeries };

type Props = { campaignId: string; sku: string };

// Разрыв тиков ставки: быстрый цикл воркера тикает раз в 5 минут круглосуточно
// (worker.py:377, run_tick пишет снапшот безусловно, до веток дневного окна),
// поэтому штатное расстояние между точками — 5 минут, а не «несколько раз в
// сутки». Порог — четыре таких интервала (20 минут): переживает пару
// пропущенных тиков (сбой сети по одной кампании run_cycle ловит и идёт
// дальше, это даёт паузу 10-15 минут, не больше) и не рвёт линию на шуме, но
// простой от получаса уже виден разрывом, а не нарисованной прямой.
const TICK_MAX_GAP_SEC = 20 * 60;
// Подневные ряды — ровно одна точка в календарный день: шаг больше одного
// дня означает пропущенный день (см. task-4-brief, «Разрыв в данных»).
const DAY_MAX_GAP = 1;

/** Календарный день "YYYY-MM-DD" → целый номер дня (UTC-эпоха в сутках).
 *  Нужен как числовая ось X для подневных рядов: пропущенный день должен
 *  дать реальный шаг 2, а не молчаливо схлопнуться до соседнего индекса
 *  массива — иначе разрыв в данных не разорвал бы линию (см. брифинг). */
function dayIndex(day: string): number {
  const [y, m, d] = day.split("-").map(Number);
  return Math.round(Date.UTC(y, m - 1, d) / 86400000);
}

function fmtDayShort(day: string): string {
  const [, m, d] = day.split("-");
  return `${d}.${m}`;
}

function ruDays(n: number): string {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "день";
  if (mod10 >= 2 && mod10 <= 4 && !(mod100 >= 12 && mod100 <= 14)) return "дня";
  return "дней";
}

const fmtTacos = (v: number) => fmtPct(v);
const fmtBid = (v: number) => `${fmtMoney(v)} ₸`;

/** Вкладка «Графики»: ставка/CPC по тикам с маркерами решений, TACoS с
 *  залитым целевым коридором, CTR и конверсия в корзину — по дням.
 *  Разметка карточек — .chart-card/.chart-head из макета
 *  (docs/design/autopilot-mockup.html), геометрия — components/LineChart. */
export default function ChartsTab({ campaignId, sku }: Props) {
  const [period, setPeriod] = useState<Period>("14");
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback((onCancelled: () => boolean) => {
    setState({ kind: "loading" });
    apiGet<ProductSeries>(`/api/products/${campaignId}/${sku}/series?days=${period}`)
      .then((series) => { if (!onCancelled()) setState({ kind: "ready", series }); })
      .catch((err) => {
        if (onCancelled()) return;
        setState({
          kind: "error",
          message: err instanceof ApiError ? err.errors.join(", ") : "Не удалось загрузить графики",
        });
      });
  }, [campaignId, sku, period]);

  useEffect(() => {
    let cancelled = false;
    load(() => cancelled);
    return () => { cancelled = true; };
  }, [load]);

  return (
    <div>
      <div className="charts-period">
        <Segmented
          ariaLabel="Период графиков"
          value={period}
          onChange={setPeriod}
          options={[
            { value: "7", label: "7 дней" },
            { value: "14", label: "14 дней" },
            { value: "30", label: "30 дней" },
          ]}
        />
      </div>

      {state.kind === "loading" && <p className="empty-list">Загрузка графиков…</p>}

      {state.kind === "error" && (
        <div>
          <p style={{ color: "var(--crit)" }}>{state.message}</p>
          <button type="button" className="btn" onClick={() => load(() => false)}>Повторить</button>
        </div>
      )}

      {state.kind === "ready" && <ChartsGrid series={state.series} />}
    </div>
  );
}

function ChartsGrid({ series }: { series: ProductSeries }) {
  const { ticks, daily, decisions, corridor } = series;

  // ---- ставка и цена клика — по тикам, своя шкала времени ---------------
  const bidSeries: ChartSeries = { key: "bid", name: "Ставка", color: "var(--s-bid)",
    data: ticks.map((t) => ({ x: t.ts, y: t.bid })) };
  const cpcSeries: ChartSeries = { key: "cpc", name: "Цена клика", color: "var(--s-cpc)",
    data: ticks.map((t) => ({ x: t.ts, y: t.avg_cpc })) };
  const markers: ChartMarker[] = decisions
    .filter((d) => d.action !== "hold" && d.new_bid !== null)
    .map((d) => ({ x: d.ts, y: d.new_bid as number, kind: d.action as "raise" | "lower", label: d.reason }));

  // ---- подневные ряды — своя шкала: день-индекс, а не порядковый номер --
  const dayLabel = new Map<number, string>();
  const dayX = (day: string) => {
    const x = dayIndex(day);
    dayLabel.set(x, day);
    return x;
  };
  const fmtDayX = (x: number) => fmtDayShort(dayLabel.get(x) ?? "");

  const tacosSeries: ChartSeries = { key: "tacos", name: "TACoS", color: "var(--s-tacos)",
    data: daily.map((d) => ({ x: dayX(d.day), y: d.tacos })) };
  const ctrSeries: ChartSeries = { key: "ctr", name: "CTR", color: "var(--s-ctr)",
    data: daily.map((d) => ({ x: dayX(d.day), y: d.ctr })) };
  const crSeries: ChartSeries = { key: "cr", name: "В корзину", color: "var(--s-cr)",
    data: daily.map((d) => ({ x: dayX(d.day), y: d.cr })) };

  const breachDays = daily.filter((d) => d.tacos !== null && d.tacos > corridor.high).length;
  const tacosSub = breachDays > 0
    ? `% от выручки · ${breachDays} ${ruDays(breachDays)} выше потолка`
    : "% от выручки";

  const hasTicks = ticks.some((t) => t.bid !== null || t.avg_cpc !== null);
  const hasTacos = daily.some((d) => d.tacos !== null);
  const hasCtr = daily.some((d) => d.ctr !== null);
  const hasCr = daily.some((d) => d.cr !== null);

  return (
    <div className="charts">
      <div className="chart-card">
        <div className="chart-head">
          <span className="chart-title">Ставка и цена клика</span>
          <span className="chart-sub">₸ · треугольники — правки биддера</span>
          <span className="legend">
            <span><i style={{ background: "var(--s-bid)" }} />Ставка</span>
            <span><i style={{ background: "var(--s-cpc)" }} />Цена клика</span>
          </span>
        </div>
        {hasTicks ? (
          <LineChart
            series={[bidSeries, cpcSeries]}
            markers={markers}
            maxGap={TICK_MAX_GAP_SEC}
            fmtValue={fmtBid}
            fmtX={(x) => fmtTs(x)}
            height={210}
          />
        ) : <p className="empty-list">данные ещё копятся</p>}
      </div>

      <div className="chart-card">
        <div className="chart-head">
          <span className="chart-title">TACoS</span>
          <span className="chart-sub">{tacosSub}</span>
        </div>
        {hasTacos ? (
          <LineChart
            series={[tacosSeries]}
            corridor={{ low: corridor.low, high: corridor.high,
                        label: `целевой коридор ${fmtPct(corridor.low)}–${fmtPct(corridor.high)}` }}
            maxGap={DAY_MAX_GAP}
            fmtValue={fmtTacos}
            fmtX={fmtDayX}
            height={190}
          />
        ) : <p className="empty-list">данные ещё копятся</p>}
      </div>

      <div className="small-mults">
        <div className="chart-card">
          <div className="chart-head">
            <span className="chart-title">CTR</span>
            <span className="chart-sub">% показов</span>
          </div>
          {hasCtr ? (
            <LineChart series={[ctrSeries]} maxGap={DAY_MAX_GAP} fmtValue={fmtTacos} fmtX={fmtDayX} height={150} />
          ) : <p className="empty-list">данные ещё копятся</p>}
        </div>
        <div className="chart-card">
          <div className="chart-head">
            <span className="chart-title">Конверсия в корзину</span>
            <span className="chart-sub">% кликов</span>
          </div>
          {hasCr ? (
            <LineChart series={[crSeries]} maxGap={DAY_MAX_GAP} fmtValue={fmtTacos} fmtX={fmtDayX} height={150} />
          ) : <p className="empty-list">данные ещё копятся</p>}
        </div>
      </div>
    </div>
  );
}
