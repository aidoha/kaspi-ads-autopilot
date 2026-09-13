import { useState } from "react";
import { linePath, scale, segments, type Point } from "./chart";

export type SeriesPoint = { x: number; y: number | null };

export type ChartSeries = {
  key: string;
  name: string;
  color: string;
  data: SeriesPoint[];
};

export type ChartMarker = {
  x: number;
  /** Значение, на уровне которого рисовать треугольник (обычно — сама
   *  ставка после решения: маркер показывает, докуда её подвинул биддер). */
  y: number;
  kind: "raise" | "lower";
  label: string;
};

export type Corridor = { low: number; high: number; label: string };

type Props = {
  series: ChartSeries[];
  /** Порог разрыва по X (в тех же единицах, что и `x` точек): день-индекс
   *  для подневных рядов, секунды — для тиков. Расстояние больше порога
   *  рвёт линию вместо соединения через пропуск. */
  maxGap: number;
  /** Формат значения — ось Y, подпись на конце линии, строка тултипа. */
  fmtValue: (v: number) => string;
  /** Формат подписи по X — дата/время точки в тултипе и под осью. */
  fmtX: (x: number) => string;
  height?: number;
  corridor?: Corridor;
  markers?: ChartMarker[];
};

const W = 760, L = 46, R = 62, T = 18, B = 30;
const MIN_LABEL_GAP = 14;

/** Линия/несколько линий с сеткой, подписями концов, кросс-хэйром и
 *  тултипом — геометрия из docs/design/autopilot-mockup.html (lineChart +
 *  mountChart), перенесённая на чистые функции chart.ts и React-события
 *  вместо ручной работы с DOM. */
export default function LineChart({
  series, maxGap, fmtValue, fmtX, height = 200, corridor, markers,
}: Props) {
  const H = height;
  const iw = W - L - R, ih = H - T - B;

  const xs = series.flatMap((s) => s.data.map((p) => p.x))
    .concat(markers ? markers.map((m) => m.x) : []);
  // Домен Y включает коридор — иначе залитая полоса TACoS могла бы вылезти
  // за верхнюю или нижнюю границу самого графика.
  const ys = series
    .flatMap((s) => s.data.map((p) => p.y))
    .filter((v): v is number => v !== null && Number.isFinite(v))
    .concat(corridor ? [corridor.low, corridor.high] : []);

  if (xs.length === 0 || ys.length === 0) return null;

  const xScale = scale(xs, iw, 0);
  const yScale = scale(ys, ih, 0.16, true);
  const X = (x: number) => L + xScale.toPx(x);
  const Y = (v: number) => T + ih - yScale.toPx(v);

  // Столбцы наведения — объединённый и отсортированный X всех серий.
  // Расстояния между ними не равны (реальные даты/тики, а не индексы), так
  // что курсор ищет ближайший в пикселях, а не по номеру колонки.
  const hoverXs = Array.from(new Set(series.flatMap((s) => s.data.map((p) => p.x)))).sort((a, b) => a - b);
  const [hoverX, setHoverX] = useState<number | null>(null);

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

  const yTicks = [yScale.min, (yScale.min + yScale.max) / 2, yScale.max];
  const xTickXs = hoverXs.length
    ? [hoverXs[0], hoverXs[Math.floor((hoverXs.length - 1) / 2)], hoverXs[hoverXs.length - 1]]
    : [];

  // Подписи на конце каждой серии — раздвигаем по Y, чтобы близкие по
  // значению линии (например ставка и цена клика под конец периода) не
  // наложили текст друг на друга.
  const endLabels = series
    .map((s) => {
      const last = [...s.data].reverse().find((p) => p.y !== null);
      if (!last || last.y === null) return null;
      return { series: s, x: X(last.x), y: Y(last.y), value: last.y };
    })
    .filter((v): v is { series: ChartSeries; x: number; y: number; value: number } => v !== null)
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < endLabels.length; i++) {
    if (endLabels[i].y - endLabels[i - 1].y < MIN_LABEL_GAP) {
      endLabels[i].y = endLabels[i - 1].y + MIN_LABEL_GAP;
    }
  }

  const hoverRow = hoverX === null ? null : series.map((s) => {
    const pt = s.data.find((p) => p.x === hoverX);
    return { name: s.name, color: s.color, text: pt && pt.y !== null ? fmtValue(pt.y) : "—" };
  });

  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" preserveAspectRatio="xMidYMid meet">
        {corridor && (
          <>
            <rect x={L} y={Y(corridor.high)} width={iw}
                  height={Math.max(0, Y(corridor.low) - Y(corridor.high))}
                  fill="var(--band)" />
            <text x={L + 6} y={Y(corridor.high) - 5} fill="var(--muted-ink)" fontSize={10.5}>
              {corridor.label}
            </text>
          </>
        )}

        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={L} y1={Y(v)} x2={L + iw} y2={Y(v)} stroke="var(--grid)" strokeWidth={1} />
            <text x={L - 9} y={Y(v) + 3.5} textAnchor="end" fill="var(--muted-ink)" fontSize={11}>
              {fmtValue(v)}
            </text>
          </g>
        ))}
        <line x1={L} y1={T + ih} x2={L + iw} y2={T + ih} stroke="var(--axis)" strokeWidth={1} />
        {xTickXs.map((x, i) => (
          <text key={i} x={X(x)} y={H - 9}
                textAnchor={i === 0 ? "start" : i === xTickXs.length - 1 ? "end" : "middle"}
                fill="var(--muted-ink)" fontSize={11}>
            {fmtX(x)}
          </text>
        ))}

        {series.map((s) => {
          const valid = s.data
            .filter((p): p is { x: number; y: number } => p.y !== null && Number.isFinite(p.y));
          const segs = segments(valid, maxGap);
          return (
            <g key={s.key}>
              {segs.map((seg, i) => {
                const pts: Point[] = seg.map((p) => ({ x: X(p.x), y: Y(p.y) }));
                return (
                  <path key={i} d={linePath(pts)} fill="none" stroke={s.color}
                        strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
                );
              })}
            </g>
          );
        })}

        {hoverX !== null && (
          <line x1={X(hoverX)} y1={T} x2={X(hoverX)} y2={T + ih} stroke="var(--axis)" strokeWidth={1} />
        )}
        {/* Прозрачный слой наведения — ПЕРЕД подписями и маркерами по
           порядку в SVG (в SVG рисуется, а значит и перехватывает события,
           последний элемент), иначе он перекрыл бы треугольники решений
           собой и их <title> с причиной никогда бы не всплыл. */}
        <rect x={L} y={T} width={iw} height={ih} fill="transparent"
              onMouseMove={handleMove} onMouseLeave={() => setHoverX(null)} />

        {endLabels.map(({ series: s, x, y, value }) => (
          <g key={s.key}>
            <circle cx={x} cy={y} r={3.4} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
            <text x={x + 9} y={y + 4} fill={s.color} fontSize={11.5} fontWeight={600}>
              {fmtValue(value)}
            </text>
          </g>
        ))}

        {markers && markers.map((m, i) => {
          const up = m.kind === "raise";
          const x = X(m.x), cy = Y(m.y) - 13;
          return (
            <path key={i}
                  d={`M${x.toFixed(1)} ${(cy - 4).toFixed(1)} l4.2 5 l-8.4 0 Z`}
                  transform={up ? undefined : `rotate(180 ${x.toFixed(1)} ${(cy - 1.5).toFixed(1)})`}
                  fill={up ? "var(--good)" : "var(--crit)"}
                  stroke="var(--surface)" strokeWidth={1.4} strokeLinejoin="round">
              <title>{m.label}</title>
            </path>
          );
        })}
      </svg>
      {hoverX !== null && hoverRow && (
        <div className="tip"
             style={{
               opacity: 1,
               left: `${Math.min(Math.max((X(hoverX) / W) * 100, 8), 92)}%`,
               top: 6,
               transform: "translateX(-50%)",
             }}>
          <b>{fmtX(hoverX)}</b><br />
          {hoverRow.map((r, i) => (
            <span key={i}>
              <span className="k">{r.name}</span> <b>{r.text}</b>{i < hoverRow.length - 1 ? <br /> : null}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
