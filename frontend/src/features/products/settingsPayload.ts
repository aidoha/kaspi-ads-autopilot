// settingsPayload.ts — сборка тела PUT .../settings из состояния формы.
//
// Вынесено отдельной чистой функцией, потому что здесь легко молча
// навредить: включённый тоггл «наследовать» обязан отправить поле как
// null (удаляет переопределение), а не как 0 — присланный ноль означал бы
// «потолок ставки = 0 ₸», и биддер перестал бы поднимать ставки вообще
// (см. task-3-brief, пункт 2).

import type { FieldName } from "./fieldMeta";

export type FieldFormState = { value: string; inherited: boolean };

/** null — наследовать (сервер удалит override). Иначе — сырая строка из
 *  input: сервер парсит её сам (`float(raw)`), и при ошибке присылает
 *  дословный русский текст («field»: нужно число...) — приводить к number
 *  здесь и терять эту строку не нужно. */
export function buildSettingsPayload(
  fields: Record<FieldName, FieldFormState>,
): Record<FieldName, string | null> {
  const out = {} as Record<FieldName, string | null>;
  for (const field of Object.keys(fields) as FieldName[]) {
    const f = fields[field];
    out[field] = f.inherited ? null : f.value;
  }
  return out;
}
