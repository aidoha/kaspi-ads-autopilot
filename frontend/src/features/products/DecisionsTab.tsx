import { fmtMoney, fmtTime } from "../../api/format";
import type { Decision } from "./types";

const ACTION_LABEL: Record<Decision["action"], string> = {
  raise: "↑ Повышение",
  lower: "↓ Снижение",
  hold: "— Без правки",
};

/** Вкладка «Решения» — таблица decisions_log за сегодня (core/store.py:
 *  get_decisions_for_sku_day, свежие сверху). Разметка — .dec/.dec-row из
 *  макета (docs/design/autopilot-mockup.html). */
export default function DecisionsTab({ decisions }: { decisions: Decision[] }) {
  if (decisions.length === 0) {
    return <p className="empty-list">Сегодня биддер ещё не принимал решений по этому товару.</p>;
  }
  return (
    <div className="dec">
      {decisions.map((d, i) => (
        <div className="dec-row" key={`${d.ts}-${i}`}>
          <span className="dec-time">{fmtTime(d.ts)}</span>
          <span><span className={`act ${d.action}`}>{ACTION_LABEL[d.action]}</span></span>
          <span className="dec-why">{d.reason}</span>
          <span className="dec-bid">
            {fmtMoney(d.old_bid)} → <b>{fmtMoney(d.new_bid)} ₸</b>
          </span>
        </div>
      ))}
    </div>
  );
}
