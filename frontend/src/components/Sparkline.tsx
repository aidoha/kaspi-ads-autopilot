/** Путь спарклайна. Вынесен из компонента, чтобы геометрию можно было
 *  проверить тестом: ошибка здесь рисует молча неверную картинку, и её
 *  никто не заметит. */
export function sparkPath(values: number[], w: number, h: number): string {
  if (values.length < 2) return "";        // одной точкой линию не построить
  const pad = 3;
  const min = Math.min(...values), max = Math.max(...values);
  // Плоский ряд — обычное дело: ставка не меняется сутками. Без этой
  // защиты (v-min)/(max-min) дало бы NaN и путь исчез бы.
  const span = max - min || 1;
  const x = (i: number) => pad + (i * (w - pad * 2)) / (values.length - 1);
  const y = (v: number) => h - pad - ((v - min) / span) * (h - pad * 2);
  return values
    .map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`)
    .join(" ");
}

type Props = {
  values: number[];
  w?: number;
  h?: number;
};

/** Спарклайн ставки в строке товара — разметка и заливка из макета
 *  (docs/design/autopilot-mockup.html, функция sparkline). */
export default function Sparkline({ values, w = 84, h = 24 }: Props) {
  const d = sparkPath(values, w, h);
  if (!d) return <svg className="spark" width={w} height={h} aria-hidden="true" />;

  const pad = 3;
  const firstX = pad.toFixed(1);
  const lastX = (pad + ((values.length - 1) * (w - pad * 2)) / (values.length - 1)).toFixed(1);
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const lastY = (h - pad - ((values[values.length - 1] - min) / span) * (h - pad * 2)).toFixed(1);
  const area = `${d} L${lastX} ${h - pad} L${firstX} ${h - pad} Z`;

  return (
    <svg className="spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <path d={area} fill="var(--band)" />
      <path d={d} fill="none" stroke="var(--s-cpc)" strokeWidth={1.6}
            strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={lastX} cy={lastY} r={2.6} fill="var(--s-cpc)"
              stroke="var(--surface)" strokeWidth={1.5} />
    </svg>
  );
}
