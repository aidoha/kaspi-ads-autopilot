import { useState } from "react";
import { apiSend, ApiError } from "../../api/client";
import Switch from "../../components/Switch";

type Props = {
  dryRun: boolean;
  /** Вызывается с новым значением ПОСЛЕ успешного POST /api/dry-run.
   *  Владение состоянием — у App (см. task-5-brief, шаг 2): шапка
   *  главного экрана и эта карточка обязаны показывать одно и то же,
   *  а не расходиться после переключения. */
  onChanged: (next: boolean) => void;
};

/** Карточка тестового режима — единственный рубильник настоящих денег в
 *  панели. Выключение dry_run значит, что биддер начинает слать реальные
 *  ставки в кабинет Kaspi — это ЕДИНСТВЕННОЕ действие здесь, которое
 *  требует подтверждения. Включение обратно только останавливает трату,
 *  подтверждения не спрашиваем: лишний диалог на безопасном действии
 *  приучает жать «да» не читая, и тогда он не сработает там, где важно
 *  (см. webui/templates/settings.html, kaAutopilotConfirmDryRun). */
export default function DryRunCard({ dryRun, onChanged }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function apply(next: boolean) {
    setError(null);
    setBusy(true);
    try {
      const res = await apiSend<{ ok: boolean; dry_run: boolean }>(
        "POST", "/api/dry-run", { dry_run: next },
      );
      onChanged(res.dry_run);
    } catch (err) {
      setError(err instanceof ApiError ? err.errors.join(", ") : "Не удалось изменить режим");
    } finally {
      setBusy(false);
    }
  }

  function handleChange(checked: boolean) {
    if (dryRun && !checked) {
      const confirmed = window.confirm(
        "Выключить тестовый режим? Биддер начнёт отправлять реальные ставки в кабинет Kaspi и тратить бюджет.",
      );
      if (!confirmed) return;
    }
    apply(checked);
  }

  return (
    <>
      <h2>Режим работы</h2>
      <div className="group">
        <div className="toggle-row">
          <div className="info">
            <strong>Тестовый режим (dry_run)</strong>
            <p>
              Включён — движок только считает решения и пишет их в лог, реальные ставки в кабинет
              Kaspi НЕ отправляются. Выключен — биддер начинает реально менять ставки и тратить бюджет.
            </p>
          </div>
          <Switch checked={dryRun} disabled={busy} onChange={handleChange} label="Тестовый режим" />
        </div>
      </div>
      {error && (
        <div className="errors" role="alert">
          <p>{error}</p>
        </div>
      )}
    </>
  );
}
