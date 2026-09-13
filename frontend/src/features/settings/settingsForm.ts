// settingsForm.ts — чистые функции экрана глобальных настроек: какие поля
// показывать в общей форме, разбор поля «id кампаний» и сборка тела
// PUT /api/settings. Вынесено отдельно, чтобы покрыть тестами (правило
// проекта: тестами покрываем только чистые функции).

/** Поля общей формы — все, кроме dry_run (отдельная карточка-рубильник,
 *  см. DryRunCard) и campaign_ids (отдельное строковое поле внизу формы,
 *  список id через запятую, а не число). */
export function visibleFields(fields: string[]): string[] {
  return fields.filter((f) => f !== "dry_run" && f !== "campaign_ids");
}

/** "id1, id2 ,,id3" → ["id1","id2","id3"]. Пустая строка (только пробелы
 *  и запятые тоже) → [] — сервер трактует пустой список как «вести все
 *  активные кампании» (core/settings_io.save_settings). */
export function parseCampaignIds(raw: string): string[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

/** ["id1","id2"] → "id1,id2" — для показа в текстовом поле. Кампании не
 *  заданы (null/undefined — ведутся все) → пустая строка. */
export function formatCampaignIds(ids: string[] | null | undefined): string {
  return (ids ?? []).join(",");
}

/** Тело PUT /api/settings: сырые строки из инпутов (сервер сам парсит
 *  float() и при ошибке присылает дословный русский текст — приводить к
 *  number здесь и терять исходную строку не нужно, см. settingsPayload.ts
 *  в products/ для того же приёма) плюс разобранный список кампаний.
 *  dry_run не шлём вовсе: PUT /api/settings это поле осознанно игнорирует
 *  (webui/api/settings.py) — оно меняется только через POST /api/dry-run. */
export function buildSettingsPayload(
  values: Record<string, string>,
  campaignIdsRaw: string,
): Record<string, unknown> {
  return { ...values, campaign_ids: parseCampaignIds(campaignIdsRaw) };
}
