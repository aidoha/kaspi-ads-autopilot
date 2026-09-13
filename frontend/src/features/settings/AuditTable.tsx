import { fmtTs } from "../../api/format";

/** Строка GET /api/audit. old/new сервер уже хранит строками
 *  (core/store.py: log_settings_change пишет str(old)/str(new)) —
 *  показываем как есть, приводить к числу не нужно. */
export type AuditRow = { ts: number; user: string; field: string; old: string; new: string };

type Props = { audit: AuditRow[] };

/** Таблица аудита правок глобальных настроек — время, пользователь,
 *  параметр, было/стало. Пусто — «правок ещё не было», как в старой
 *  Jinja-панели (webui/templates/settings.html). */
export default function AuditTable({ audit }: Props) {
  return (
    <>
      <h2>Аудит изменений</h2>
      <div className="group">
        {audit.length === 0 ? (
          <p className="empty-list">Правок ещё не было.</p>
        ) : (
          <div className="audit-wrap">
            <table className="audit">
              <thead>
                <tr>
                  <th>Время</th>
                  <th>Пользователь</th>
                  <th>Параметр</th>
                  <th>Было</th>
                  <th>Стало</th>
                </tr>
              </thead>
              <tbody>
                {audit.map((row, i) => (
                  <tr key={`${row.ts}-${row.field}-${i}`}>
                    <td>{fmtTs(row.ts)}</td>
                    <td>{row.user}</td>
                    <td>{row.field}</td>
                    <td>{row.old}</td>
                    <td>{row.new}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
