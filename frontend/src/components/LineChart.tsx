import { useEffect, useRef, useState } from "react";
import { areaPath, axisTicks, linePath, niceTicks, segments, type Point } from "./chart";

export type SeriesPoint = { x: number; y: number | null };

export type ChartSeries = {
  key: string;
  name: string;
  color: string;
  data: SeriesPoint[];
  /** Заливка под линией (мягкая, ~10% цвета серии). Включается у той серии,
   *  которая в карточке главная: две наложенные заливки дают грязь, в
   *  которой не читается ни одна. */
  area?: boolean;
};

export type ChartMarker = {
  x: number;
  /** Ставка после решения. На полотно больше не выносится (маркеры живут в
   *  отдельной дорожке под графиком), но едет в тултип: «подняли до 42 ₸». */
  y: number;
  kind: "raise" | "lower";
  label: string;
};

/** Целевая полоса-подложка. `label` на полотно не выводится (любое место
 *  внутри графика рано или поздно занимает линия) — он уходит в подпись
 *  карточки и в описание для скринридера. */
export type Corridor = { low: number; high: number; label: string };

type Props = {
  series: ChartSeries[];
  /** Порог разрыва по X (в тех же единицах, что и `x` точек): день-индекс
   *  для подневных рядов, секунды — для тиков. Расстояние больше порога
   *  рвёт линию вместо соединения через пропуск. */
  maxGap: number;
  /** Формат значения — ось Y, подпись на конце линии, строка тултипа. */
  fmtValue: (v: number) => string;
  /** Формат подписи по X в тултипе — конкретный момент точки. */
  fmtX: (x: number) => string;
  /** Формат подписи под осью X. По умолчанию совпадает с fmtX, но у оси
   *  деления круглые, и им идёт короткая форма («06.09» вместо
   *  «06.09, 00:00»): под осью нужна опора для глаза, а не точное время. */
  fmtXAxis?: (x: number) => string;
  /** Допустимые шаги решётки оси X, от мелкого к крупному, в единицах X. */
  xSteps: number[];
  /** Сдвиг решётки оси X (для тиков — полночь Алматы). */
  xOrigin?: number;
  height?: number;
  corridor?: Corridor;
  markers?: ChartMarker[];
};

const L = 52, R = 66, T = 14, B = 26;
const RAIL = 16;          // дорожка маркеров между полотном и подписями оси X
const MIN_LABEL_GAP = 15;
const DOTS_MAX = 40;      // больше точек — узлы сливаются в пунктир, не рисуем

/** Линия/несколько линий с сеткой по круглым делениям, мягкой заливкой,
 *  узлами, кросс-хэйром и плавным тултипом.
 *
 *  Полотно меряется по факту (ResizeObserver), а не растягивается из
 *  фиксированного viewBox: раньше SVG шириной 760 растягивался на всю
 *  карточку (~1180px), и вместе с ним в полтора раза росли шрифты и толщина
 *  линий — отсюда были гигантские подписи и жирные штрихи. */
export default function LineChart({
  series, maxGap, fmtValue, fmtX, fmtXAxis, xSteps, xOrigin = 0, height = 200, corridor, markers,
}: Props) {
  // Хуки объявлены до любого раннего выхода: ниже есть `return null` для
  // пустых данных, и вызов хука после него менял бы их порядок между
  // рендерами — React роняет такой переход («rendered fewer hooks»).
  const [hoverX, setHoverX] = useState<number | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = useState(0);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const apply = (px: number) => { if (px > 0) setMeasured(Math.round(px)); };
    apply(el.getBoundingClientRect().width);
    const ro = new ResizeObserver((entries) => apply(entries[0].contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // До первого замера (вкладка ещё скрыта — ширина 0) рисуем по разумной
  // ширине, чтобы не мигать пустотой; ResizeObserver поправит в тот же кадр.
  const W = measured || 760;
  const hasRail = !!(markers && markers.length);
  const H = height + (hasRail ? RAIL : 0);
  const iw = W - L - R, ih = H - T - B - (hasRail ? RAIL : 0);

  const xs = series.flatMap((s) => s.data.map((p) => p.x));
  // Домен Y включает коридор — иначе залитая полоса TACoS могла бы вылезти
  // за верхнюю или нижнюю границу самого графика.
  const ys = series
    .flatMap((s) => s.data.map((p) => p.y))
    .filter((v): v is number => v !== null && Number.isFinite(v))
    .concat(corridor ? [corridor.low, corridor.high] : []);

  if (xs.length === 0 || ys.length === 0) return null;

  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const xSpan = xMax - xMin || 1;
  // Предел делений — по высоте полотна: в низкой карточке малого графика
  // пять интервалов сжимают подписи, в высокой четыре огрубляют шаг на
  // разряд и оставляют пустую треть над данными.
  const yAxis = niceTicks(Math.min(...ys), Math.max(...ys), ih < 130 ? 4 : 5);
  const ySpan = yAxis.max - yAxis.min || 1;

  const X = (x: number) => L + ((x - xMin) / xSpan) * iw;
  const Y = (v: number) => T + ih - ((v - yAxis.min) / ySpan) * ih;
  const baseY = T + ih;

  // Столбцы наведения — объединённый и отсортированный X всех серий.
  // Расстояния между ними не равны (реальные даты/тики, а не индексы), так
  // что курсор ищет ближайший в пикселях, а не по номеру колонки.
  const hoverXs = Array.from(new Set(series.flatMap((s) => s.data.map((p) => p.x)))).sort((a, b) => a - b);

  function handleMove(e: React.MouseEvent<SVGRectElement>) {
    if (hoverXs.length === 0) return;
    const rect = e.currentTarget.ownerSVGElement!.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    let best = hoverXs[0], bestDist = Infinity;
    for (const x of hoverXs) {
      const d = Math.abs(X(x) - px);
      if (d < bestDist) { bestDist = d; best = x; }
    }
    setHoverX(best);
  }

  // Сколько делений влезает: подпись даты/времени занимает ~40px, между
  // соседними нужен воздух вчетверо больше — отсюда пороги.
  const xTicks = axisTicks(xMin, xMax, xSteps, xOrigin, W < 560 ? 4 : W < 900 ? 6 : 8);
  const showDots = hoverXs.length <= DOTS_MAX;

  // Подписи на конце каждой серии — раздвигаем по Y, чтобы близкие по
  // значению линии (например ставка и цена клика под конец периода) не
  // наложили текст друг на друга.
  const endLabels = series
    .map((s) => {
      const last = [...s.data].reverse().find((p) => p.y !== null);
      if (!last || last.y === null) return null;
      return { series: s, x: X(last.x), y: Y(last.y), lineY: Y(last.y), value: last.y };
    })
    .filter((v): v is { series: ChartSeries; x: number; y: number; lineY: number; value: number } => v !== null)
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < endLabels.length; i++) {
    if (endLabels[i].y - endLabels[i - 1].y < MIN_LABEL_GAP) {
      endLabels[i].y = endLabels[i - 1].y + MIN_LABEL_GAP;
    }
  }

  const hoverPoints = hoverX === null ? [] : series
    .map((s) => {
      const pt = s.data.find((p) => p.x === hoverX);
      return pt && pt.y !== null && Number.isFinite(pt.y)
        ? { key: s.key, color: s.color, name: s.name, y: Y(pt.y), text: fmtValue(pt.y) }
        : { key: s.key, color: s.color, name: s.name, y: null, text: "—" };
    });
  // Решение, попавшее в наведённый столбец, показываем текстом: маркер в
  // дорожке говорит ЧТО произошло, а ценность графика — в ПОЧЕМУ.
  const hoverMarks = hoverX === null || !markers ? [] :
    markers.filter((m) => Math.abs(m.x - hoverX) <= maxGap / 2);

  return (
    <div className="chart-wrap" ref={wrapRef}>
      {/* Обрезка — на этом внутреннем слое, а не на .chart-wrap: тултип
         лежит рядом и у края графика выезжает за полотно, а общий
         overflow:hidden срезал бы его. */}
      <div className="chart-plot">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label={[series.map((s) => s.name).join(", "), corridor?.label].filter(Boolean).join(" · ")}>
        {corridor && (
          <>
            <rect x={L} y={Y(corridor.high)} width={iw}
                  height={Math.max(0, Y(corridor.low) - Y(corridor.high))}
                  fill="var(--corridor)" />
            <line x1={L} y1={Y(corridor.high)} x2={L + iw} y2={Y(corridor.high)}
                  stroke="var(--corridor-edge)" strokeWidth={1} />
            <line x1={L} y1={Y(corridor.low)} x2={L + iw} y2={Y(corridor.low)}
                  stroke="var(--corridor-edge)" strokeWidth={1} />
          </>
        )}

        {yAxis.ticks.map((v, i) => (
          <g key={i}>
            <line x1={L} y1={Y(v)} x2={L + iw} y2={Y(v)} stroke="var(--grid)" strokeWidth={1}
                  shapeRendering="crispEdges" />
            <text x={L - 10} y={Y(v) + 3.5} textAnchor="end" fill="var(--ink-3)" fontSize={11}
                  className="ax">
              {fmtValue(v)}
            </text>
          </g>
        ))}
        <line x1={L} y1={baseY} x2={L + iw} y2={baseY} stroke="var(--axis)" strokeWidth={1}
              shapeRendering="crispEdges" />
        {xTicks.map((x, i) => (
          // Крайние подписи прижимаем к краю полотна, а не центрируем: по
          // центру левая наезжает на числа оси Y, правая — на подписи концов.
          <text key={i} x={X(x)} y={H - 7}
                textAnchor={X(x) - L < 18 ? "start" : L + iw - X(x) < 18 ? "end" : "middle"}
                fill="var(--ink-3)" fontSize={11} className="ax">
            {(fmtXAxis ?? fmtX)(x)}
          </text>
        ))}

        {series.map((s) => {
          const valid = s.data
            .filter((p): p is { x: number; y: number } => p.y !== null && Number.isFinite(p.y));
          const segs = segments(valid, maxGap);
          const px = (seg: { x: number; y: number }[]): Point[] =>
            seg.map((p) => ({ x: X(p.x), y: Y(p.y) }));
          return (
            <g key={s.key}>
              {s.area && segs.map((seg, i) => (
                <path key={`a${i}`} d={areaPath(px(seg), baseY)} fill={s.color} opacity={0.1} />
              ))}
              {segs.map((seg, i) => (
                <path key={`l${i}`} d={linePath(px(seg))} fill="none" stroke={s.color}
                      strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
              ))}
              {showDots && valid.map((p, i) => (
                <circle key={`d${i}`} cx={X(p.x)} cy={Y(p.y)} r={3.2} fill={s.color}
                        stroke="var(--surface)" strokeWidth={2} />
              ))}
            </g>
          );
        })}

        {hoverX !== null && (
          <g className="xhair">
            <line x1={X(hoverX)} y1={T} x2={X(hoverX)} y2={baseY}
                  stroke="var(--axis)" strokeWidth={1} />
            {hoverPoints.map((p) => p.y === null ? null : (
              <g key={p.key} className="xdot" style={{ transform: `translate(${X(hoverX)}px, ${p.y}px)` }}>
                <circle r={4} fill={p.color} stroke="var(--surface)" strokeWidth={2} />
              </g>
            ))}
          </g>
        )}

        {/* Прозрачный слой наведения — ПЕРЕД подписями и дорожкой маркеров
           по порядку в SVG (в SVG рисуется, а значит и перехватывает
           события, последний элемент), иначе он перекрыл бы треугольники
           решений собой и их <title> с причиной никогда бы не всплыл. */}
        <rect x={L} y={T} width={iw} height={ih} fill="transparent"
              onMouseMove={handleMove} onMouseLeave={() => setHoverX(null)} />

        {endLabels.map(({ series: s, x, y, lineY, value }) => (
          <g key={s.key}>
            {/* Выноска — когда подпись отодвинули от её линии, чтобы соседняя
               не легла сверху: без неё сдвинутое число «отрывается» от ряда
               и читается как чужое. */}
            {Math.abs(y - lineY) > 1 && (
              <path d={`M${(x + 5).toFixed(1)} ${lineY.toFixed(1)} L${(x + 9).toFixed(1)} ${y.toFixed(1)}`}
                    stroke="var(--hairline-2)" strokeWidth={1} fill="none" />
            )}
            <circle cx={x} cy={lineY} r={4} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
            <text x={x + 11} y={y + 4} fill="var(--ink)" fontSize={11.5} fontWeight={600} className="ax">
              {fmtValue(value)}
            </text>
          </g>
        ))}

        {hasRail && markers!.map((m, i) => {
          const up = m.kind === "raise";
          const x = X(m.x), cy = baseY + RAIL / 2 + 1;
          // Треугольник вершиной вверх/вниз, 8px в основании. Дорожка под
          // полотном вместо привязки к значению: при 14-30 днях правок
          // набирается несколько десятков, и на самом графике они ложились
          // друг на друга и на линии — конфетти поверх данных.
          const d = up
            ? `M${x.toFixed(1)} ${(cy - 3.4).toFixed(1)} l4 5.6 l-8 0 Z`
            : `M${x.toFixed(1)} ${(cy + 3.4).toFixed(1)} l4 -5.6 l-8 0 Z`;
          return (
            <path key={i} d={d} fill={up ? "var(--good)" : "var(--crit)"}>
              <title>{`${fmtX(m.x)} · ${m.label}`}</title>
            </path>
          );
        })}
      </svg>
      </div>
      <div className={`tip${hoverX === null ? "" : " on"}`}
           style={hoverX === null ? undefined : {
             left: `${Math.min(Math.max((X(hoverX) / W) * 100, 9), 91)}%`,
           }}>
        {hoverX !== null && (
          <>
            <b>{fmtX(hoverX)}</b>
            {hoverPoints.map((p) => (
              <span key={p.key} className="tip-row">
                <i style={{ background: p.color }} />
                <span className="k">{p.name}</span>
                <b>{p.text}</b>
              </span>
            ))}
            {hoverMarks.map((m, i) => (
              <span key={`m${i}`} className="tip-note">
                {m.kind === "raise" ? "▲" : "▼"} {m.label}
              </span>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
