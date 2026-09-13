import { useEffect, useState } from "react";
import { apiSend, ApiError } from "../../api/client";
import Switch from "../../components/Switch";
import { FIELD_META, OVERRIDABLE_FIELDS, WEEKDAYS, type FieldName } from "./fieldMeta";
import { buildSettingsPayload, type FieldFormState } from "./settingsPayload";
import type { Preview, PreviewLoop, ProductControl, ProductDetail } from "./types";

type Props = {
  campaignId: string;
  sku: string;
  detail: ProductDetail;
  /** Перезагрузить карточку товара после успешного сохранения. Владение
   *  detail — у ProductPanel: что именно теперь «унаследовано», решает
   *  ответ сервера, а не локальная догадка вкладки. */
  onSaved: () => void;
};

function initFields(detail: ProductDetail): Record<FieldName, FieldFormState> {
  const out = {} as Record<FieldName, FieldFormState>;
  for (const f of OVERRIDABLE_FIELDS) {
    out[f] = { value: String(detail.values[f]), inherited: !detail.owned.includes(f) };
  }
  return out;
}

const ACTION_RU: Record<string, string> = {
  raise: "поднять ставку", lower: "снизить ставку", hold: "не менять",
};

type PreviewState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; preview: Preview };

/** Вкладка «Настройки» — 14 полей порогов (.fields/.field из макета),
 *  блок расписания и кнопка «Проверить сейчас». Тексты полей — из
 *  fieldMeta.ts (перенос из webui/templates/sku_settings.html). */
export default function SettingsTab({ campaignId, sku, detail, onSaved }: Props) {
  const [fields, setFields] = useState<Record<FieldName, FieldFormState>>(() => initFields(detail));
  const [savingFields, setSavingFields] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<string[]>([]);

  const [control, setControl] = useState<ProductControl>(detail.control);
  const [savingControl, setSavingControl] = useState(false);
  const [controlErrors, setControlErrors] = useState<string[]>([]);

  const [preview, setPreview] = useState<PreviewState>({ kind: "idle" });

  // Свежая карточка пришла сверху (первая загрузка или после сохранения) —
  // синхронизируем черновики. Без этого возврат «наследовать» продолжал бы
  // показывать старое число вместо того, что реально унаследовано сейчас.
  useEffect(() => {
    setFields(initFields(detail));
    setControl(detail.control);
  }, [detail]);

  function setField(name: FieldName, patch: Partial<FieldFormState>) {
    setFields((s) => ({ ...s, [name]: { ...s[name], ...patch } }));
  }

  async function saveFields(e: React.FormEvent) {
    e.preventDefault();
    setFieldErrors([]);
    setSavingFields(true);
    try {
      await apiSend("PUT", `/api/products/${campaignId}/${sku}/settings`, {
        values: buildSettingsPayload(fields),
      });
      onSaved();
    } catch (err) {
      setFieldErrors(err instanceof ApiError ? err.errors : ["Не удалось сохранить настройки"]);
    } finally {
      setSavingFields(false);
    }
  }

  async function saveControl(e: React.FormEvent) {
    e.preventDefault();
    setControlErrors([]);
    setSavingControl(true);
    try {
      await apiSend("PUT", `/api/products/${campaignId}/${sku}/control`, {
        enabled: control.enabled,
        window_start: control.window_start,
        window_end: control.window_end,
        days_mask: control.days_mask,
      });
      onSaved();
    } catch (err) {
      setControlErrors(err instanceof ApiError ? err.errors : ["Не удалось сохранить расписание"]);
    } finally {
      setSavingControl(false);
    }
  }

  async function runPreview() {
    setPreview({ kind: "loading" });
    try {
      const res = await apiSend<{ preview: Preview }>(
        "POST", `/api/products/${campaignId}/${sku}/preview`,
      );
      setPreview({ kind: "ready", preview: res.preview });
    } catch (err) {
      setPreview({
        kind: "error",
        message: err instanceof ApiError ? err.errors.join(", ") : "Не удалось проверить",
      });
    }
  }

  function toggleDay(i: number) {
    setControl((c) => ({ ...c, days_mask: c.days_mask ^ (1 << i) }));
  }

  return (
    <div>
      <form onSubmit={saveFields}>
        {fieldErrors.length > 0 && (
          <div className="errors" role="alert">
            <p>Настройки не сохранены — исправьте ошибки:</p>
            <ul>{fieldErrors.map((e, i) => <li key={i}>{e}</li>)}</ul>
          </div>
        )}
        <div className="fields">
          {OVERRIDABLE_FIELDS.map((f) => {
            const meta = FIELD_META[f];
            const st = fields[f];
            const inputId = `${sku}-${f}`;
            return (
              <div className="field" key={f}>
                <label className="field-label" htmlFor={inputId}>
                  {meta.label}
                  <small>{meta.hint}</small>
                </label>
                <input
                  type="number" step="any" id={inputId}
                  value={st.value}
                  disabled={st.inherited}
                  onChange={(e) => setField(f, { value: e.target.value })}
                />
                <span className="inh">
                  <span>наследовать</span>
                  <label className="switch sm">
                    <input
                      type="checkbox"
                      checked={st.inherited}
                      aria-label={`Наследовать «${meta.label}»`}
                      onChange={(e) => setField(f, { inherited: e.target.checked })}
                    />
                    <span className="track" /><span className="knob" />
                  </label>
                </span>
              </div>
            );
          })}
        </div>
        <div className="detail-top" style={{ margin: "18px 0 0" }}>
          <button className="btn btn-primary" type="submit" disabled={savingFields}>
            {savingFields ? "Сохраняем…" : "Сохранить"}
          </button>
        </div>
      </form>

      <h3 className="sched-h">Управление и расписание</h3>
      <form onSubmit={saveControl}>
        {controlErrors.length > 0 && (
          <div className="errors" role="alert">
            <p>Расписание не сохранено — исправьте ошибки:</p>
            <ul>{controlErrors.map((e, i) => <li key={i}>{e}</li>)}</ul>
          </div>
        )}
        <div className="sched">
          <div className="sched-row">
            <span className="field-label">
              Биддер активен для товара
              <small>Вне окна биддер ставит ставку в минимум — реклама не выключается.</small>
            </span>
            <Switch
              checked={control.enabled}
              onChange={(v) => setControl((c) => ({ ...c, enabled: v }))}
              label="Биддер активен для товара"
            />
          </div>
          <div className="sched-row">
            <span className="field-label">Рабочее окно (Алматы)</span>
            <span className="sched-window">
              с
              <input
                type="number" min={0} max={23} value={control.window_start}
                aria-label="Начало окна, час"
                onChange={(e) => setControl((c) => ({ ...c, window_start: Number(e.target.value) }))}
              />
              ч до
              <input
                type="number" min={1} max={24} value={control.window_end}
                aria-label="Конец окна, час"
                onChange={(e) => setControl((c) => ({ ...c, window_end: Number(e.target.value) }))}
              />
              ч
            </span>
          </div>
          <div className="sched-row">
            <span className="field-label">Дни недели</span>
            <div className="weekdays" role="group" aria-label="Дни недели">
              {WEEKDAYS.map((wd, i) => (
                <button
                  type="button" key={wd} className="wd-btn"
                  aria-pressed={(control.days_mask & (1 << i)) !== 0}
                  onClick={() => toggleDay(i)}
                >
                  {wd}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="detail-top" style={{ margin: "14px 0 0" }}>
          <button className="btn btn-primary" type="submit" disabled={savingControl}>
            {savingControl ? "Сохраняем…" : "Сохранить расписание"}
          </button>
        </div>
      </form>

      <div className="preview-block">
        <button className="btn" type="button" onClick={runPreview} disabled={preview.kind === "loading"}>
          {preview.kind === "loading" ? "Проверяем…" : "Проверить сейчас"}
        </button>
        <p className="preview-note">
          Это предсказание по последнему снапшоту: ничего не отправлено, ставка не изменена.
        </p>
        {preview.kind === "error" && <p style={{ color: "var(--crit)" }}>{preview.message}</p>}
        {preview.kind === "ready" && <PreviewResult preview={preview.preview} />}
      </div>
    </div>
  );
}

function loopLine(label: string, loop: PreviewLoop) {
  return <li><b>{label}:</b> {ACTION_RU[loop.action] ?? loop.action} — {loop.reason}</li>;
}

function PreviewResult({ preview }: { preview: Preview }) {
  if (preview === null) {
    return <p className="preview-result">По товару ещё нет данных для предсказания.</p>;
  }
  if ("control" in preview) {
    return <ul className="preview-result">{loopLine("Контрольный слой", preview.control)}</ul>;
  }
  return (
    <ul className="preview-result">
      {loopLine("Быстрый контур", preview.fast)}
      {loopLine("Медленный контур", preview.slow)}
    </ul>
  );
}
