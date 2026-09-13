type Props = {
  health: "good" | "warn" | "crit" | "na";
  children: React.ReactNode;
};

/** Тег TACoS из макета (.tag.good/.warn/.crit,
 *  docs/design/autopilot-mockup.html). "na" — величина не определена
 *  (нет выручки за период), рисуется как обычный текст без подкраски. */
export default function Tag({ health, children }: Props) {
  if (health === "na") return <span className="num">{children}</span>;
  return <span className={`tag ${health}`}>{children}</span>;
}
