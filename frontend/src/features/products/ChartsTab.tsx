import { useCallback, useEffect, useState } from "react";
import { apiGet, ApiError } from "../../api/client";
import { fmtMoney, fmtPct } from "../../api/format";
import type { ChartMarker, ChartSeries } from "../../components/LineChart";
import LineChart from "../../components/LineChart";
import Segmented from "../../components/Segmented";
import type { ProductSeries } from "./types";
import { dailyMarkers, dailyMedian } from "./daySeries";

type Period = "7" | "14" | "30";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; series: ProductSeries };

type Props = { campaignId: string; sku: string };

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

/** Номер дня → «06.09». Обратная к dayIndex, а не выборка из карты дней:
 *  деления оси стоят на круглых датах, и среди них попадаются дни, которых
 *  в данных нет (пропуск сбора) — по карте такая подпись вышла бы пустой. */
function fmtDayIndex(x: number): string {
  const dt = new Date(x * 86400000);
  const dd = String(dt.getUTCDate()).padStart(2, "0");
  const mm = String(dt.getUTCMonth() + 1).padStart(2, "0");
  return `${dd}.${mm}`;
}

/** Шаги решётки оси X для подневных рядов, в днях. Шаги 4 и 5 нужны узким карточкам
 *  малых графиков (CTR, конверсия): там влезает четыре подписи, и без них
 *  лестница прыгала с трёх суток сразу на неделю — на двухнедельном
 *  периоде под осью оставалось две даты. */
const DAY_X_STEPS = [1, 2, 3, 4, 5, 7, 14, 28];


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

  // ---- ставка и цена клика — точка на сутки, как остальные графики -------
  // Раньше сюда уходили все 5-минутные тики воркера: ступени ночного
  // дейпарта (ставка падает в пол) рисовали штрихкод-гребень вместо линии.
  // Сводим к суткам (медиана — не съедет от ночного пола), и график
  // получает тот же вид, что TACoS/CTR: плавная линия, точки на узлах,
  // мягкая заливка. Ось X — та же день-индексная шкала, что у подневных.
  const bidSeries: ChartSeries = { key: "bid", name: "Ставка", color: "var(--s-bid)", area: true,
    data: dailyMedian(ticks, (t) => t.bid) };
  const cpcSeries: ChartSeries = { key: "cpc", name: "Цена клика", color: "var(--s-cpc)",
    data: dailyMedian(ticks, (t) => t.avg_cpc) };
  const markers: ChartMarker[] = dailyMarkers(decisions);

  // ---- подневные ряды — своя шкала: день-индекс, а не порядковый номер --
  const dayX = (day: string) => dayIndex(day);

  const tacosSeries: ChartSeries = { key: "tacos", name: "TACoS", color: "var(--s-tacos)", area: true,
    data: daily.map((d) => ({ x: dayX(d.day), y: d.tacos })) };
  const ctrSeries: ChartSeries = { key: "ctr", name: "CTR", color: "var(--s-ctr)", area: true,
    data: daily.map((d) => ({ x: dayX(d.day), y: d.ctr })) };
  const crSeries: ChartSeries = { key: "cr", name: "В корзину", color: "var(--s-cr)", area: true,
    data: daily.map((d) => ({ x: dayX(d.day), y: d.cr })) };

  const breachDays = daily.filter((d) => d.tacos !== null && d.tacos > corridor.high).length;
  // Коридор называется в подписи карточки, а не внутри полотна: там любое
  // место рано или поздно занимает сама линия и текст ложится поверх неё.
  const corridorLabel = `целевой коридор ${fmtPct(corridor.low)}–${fmtPct(corridor.high)}`;
  const tacosSub = breachDays > 0
    ? `${corridorLabel} · ${breachDays} ${ruDays(breachDays)} выше потолка`
    : corridorLabel;

  const hasTicks = bidSeries.data.length > 0 || cpcSeries.data.length > 0;
  const hasTacos = daily.some((d) => d.tacos !== null);
  const hasCtr = daily.some((d) => d.ctr !== null);
  const hasCr = daily.some((d) => d.cr !== null);

  return (
    <div className="charts">
      <div className="chart-card">
        <div className="chart-head">
          <span className="chart-title">Ставка и цена клика</span>
          <span className="chart-sub">₸ · ▲▼ под графиком — правки биддера</span>
          <span className="legend">
            <span><i style={{ background: "var(--s-bid)" }} />Ставка</span>
            <span><i style={{ background: "var(--s-cpc)" }} />Цена клика</span>
          </span>
        </div>
        {hasTicks ? (
          <LineChart
            series={[bidSeries, cpcSeries]}
            markers={markers}
            maxGap={DAY_MAX_GAP}
            fmtValue={fmtBid}
            fmtX={fmtDayIndex}
            xSteps={DAY_X_STEPS}
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
            corridor={{ low: corridor.low, high: corridor.high, label: corridorLabel }}
            maxGap={DAY_MAX_GAP}
            fmtValue={fmtTacos}
            fmtX={fmtDayIndex}
            xSteps={DAY_X_STEPS}
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
            <LineChart series={[ctrSeries]} maxGap={DAY_MAX_GAP} fmtValue={fmtTacos}
                       fmtX={fmtDayIndex} xSteps={DAY_X_STEPS} height={150} />
          ) : <p className="empty-list">данные ещё копятся</p>}
        </div>
        <div className="chart-card">
          <div className="chart-head">
            <span className="chart-title">Конверсия в корзину</span>
            <span className="chart-sub">% кликов</span>
          </div>
          {hasCr ? (
            <LineChart series={[crSeries]} maxGap={DAY_MAX_GAP} fmtValue={fmtTacos}
                       fmtX={fmtDayIndex} xSteps={DAY_X_STEPS} height={150} />
          ) : <p className="empty-list">данные ещё копятся</p>}
        </div>
      </div>
    </div>
  );
}
