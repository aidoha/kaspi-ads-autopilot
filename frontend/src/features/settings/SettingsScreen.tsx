import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend, ApiError } from "../../api/client";
import "../../styles/products.css";
import AuditTable, { type AuditRow } from "./AuditTable";
import DryRunCard from "./DryRunCard";
import { SETTINGS_FIELD_META, TACOS_WINDOW_PRESETS } from "./fieldMeta";
import { buildSettingsPayload, formatCampaignIds, visibleFields } from "./settingsForm";

type SettingsResponse = { settings: Record<string, unknown>; fields: string[] };

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; fields: string[]; values: Record<string, string>; campaignIds: string };

type Props = {
  /** null, пока App ещё не узнал текущий режим (первая загрузка). */
  dryRun: boolean | null;
  /** Поднимает режим в общее состояние приложения — его же видит шапка
   *  главного экрана (см. task-5-brief, шаг 2). */
  onDryRunChange: (next: boolean) => void;
  onBack: () => void;
  onLogout: () => void;
};

/** Экран глобальных настроек биддера: пороги для всех товаров, тестовый
 *  режим и аудит правок. В утверждённом макете (docs/design/autopilot-
 *  mockup.html) этого экрана нет — он показывает только товары. Вёрстка на
 *  тех же токенах и приёмах: сгруппированная плашка с хайрлайнами между
 *  строками (.group, как список товаров и вкладка настроек товара),
 *  iOS-тоггл (Switch) вместо чекбокса, заголовок как на главной
 *  (.pagehead/h1). Подписи и пояснения полей — из fieldMeta.ts, перенесены
 *  дословно из webui/templates/settings.html. */
export default function SettingsScreen({ dryRun, onDryRunChange, onBack, onLogout }: Props) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [saveErrors, setSaveErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [auditError, setAuditError] = useState<string | null>(null);

  const loadAudit = useCallback(() => {
    apiGet<{ audit: AuditRow[] }>("/api/audit")
      .then((res) => { setAudit(res.audit); setAuditError(null); })
      .catch((err) => {
        setAuditError(err instanceof ApiError ? err.errors.join(", ") : "Не удалось загрузить аудит");
      });
  }, []);

  const load = useCallback(() => {
    setState({ kind: "loading" });
    apiGet<SettingsResponse>("/api/settings")
      .then((res) => {
        const values: Record<string, string> = {};
        for (const f of visibleFields(res.fields)) {
          values[f] = String(res.settings[f]);
        }
        setState({
          kind: "ready",
          fields: res.fields,
          values,
          campaignIds: formatCampaignIds(res.settings.campaign_ids as string[] | null | undefined),
        });
        // Свежее значение dry_run пришло попутно с формой — синхронизируем
        // общее состояние, а не ждём отдельного похода за ним.
        onDryRunChange(Boolean(res.settings.dry_run));
      })
      .catch((err) => {
        setState({
          kind: "error",
          message: err instanceof ApiError ? err.errors.join(", ") : "Панель не может связаться с сервером",
        });
      });
  }, [onDryRunChange]);

  useEffect(() => {
    load();
    loadAudit();
  }, [load, loadAudit]);

  if (state.kind === "loading") {
    return <div style={{ padding: 48, textAlign: "center", color: "var(--ink-2)" }}>Загрузка…</div>;
  }

  if (state.kind === "error") {
    return (
      <div style={{ padding: 48, textAlign: "center" }}>
        <p style={{ color: "var(--crit)" }}>{state.message}</p>
        <button type="button" className="btn" onClick={load}>Повторить</button>
      </div>
    );
  }

  const { values, campaignIds } = state;
  const fieldNames = visibleFields(state.fields);

  function setValue(name: string, v: string) {
    setState((s) => (s.kind !== "ready" ? s : { ...s, values: { ...s.values, [name]: v } }));
  }
  function setCampaignIds(v: string) {
    setState((s) => (s.kind !== "ready" ? s : { ...s, campaignIds: v }));
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaveErrors([]);
    setSaving(true);
    try {
      await apiSend("PUT", "/api/settings", { settings: buildSettingsPayload(values, campaignIds) });
      load();
      loadAudit();
    } catch (err) {
      setSaveErrors(err instanceof ApiError ? err.errors : ["Не удалось сохранить настройки"]);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="brand-dot" aria-hidden="true" />
            Автопилот ставок
          </div>
          {dryRun && <span className="pill-run">Тестовый режим</span>}
          <button type="button" className="linkish" onClick={onBack}>← Товары</button>
          <button type="button" className="linkish" onClick={onLogout}>Выйти</button>
        </div>
      </div>

      <div className="wrap">
        <div className="pagehead">
          <h1>Настройки биддера</h1>
          <p className="sub">
            Правки пишутся в <code>config/rules.yaml</code> — воркер подхватывает их на следующем
            цикле, без перезапуска.
          </p>
        </div>

        <form onSubmit={save}>
          {saveErrors.length > 0 && (
            <div className="errors" role="alert">
              <p>Настройки не сохранены — исправьте ошибки:</p>
              <ul>{saveErrors.map((e, i) => <li key={i}>{e}</li>)}</ul>
            </div>
          )}

          <div className="group">
            {fieldNames.map((f) => {
              const meta = SETTINGS_FIELD_META[f];
              const inputId = `settings-${f}`;
              return (
                <div className="gfield" key={f}>
                  <label className="gfield-label" htmlFor={inputId}>
                    {meta?.label ?? f}
                    {meta?.hint && <small>{meta.hint}</small>}
                  </label>
                  <input
                    type="number"
                    step={f === "tacos_window_days" ? 1 : "any"}
                    min={f === "tacos_window_days" ? 1 : undefined}
                    max={f === "tacos_window_days" ? 90 : undefined}
                    list={f === "tacos_window_days" ? "tacos-window-presets" : undefined}
                    id={inputId}
                    required
                    value={values[f] ?? ""}
                    onChange={(e) => setValue(f, e.target.value)}
                  />
                </div>
              );
            })}
            <datalist id="tacos-window-presets">
              {TACOS_WINDOW_PRESETS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </datalist>

            <div className="gfield">
              <label className="gfield-label" htmlFor="settings-campaign-ids">
                ID кампаний (через запятую)
                <small>
                  Список кампаний, которые ведёт биддер. Пусто — вести все активные (Enabled)
                  кампании маркетинга.
                </small>
              </label>
              <input
                type="text"
                id="settings-campaign-ids"
                placeholder="2899523,3032419"
                value={campaignIds}
                onChange={(e) => setCampaignIds(e.target.value)}
              />
            </div>
          </div>

          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? "Сохраняем…" : "Сохранить"}
            </button>
          </div>
        </form>

        <DryRunCard
          dryRun={dryRun ?? false}
          onChanged={(next) => {
            onDryRunChange(next);
            loadAudit();
          }}
        />

        {auditError ? (
          <p style={{ color: "var(--crit)" }}>{auditError}</p>
        ) : (
          <AuditTable audit={audit} />
        )}
      </div>
    </div>
  );
}
