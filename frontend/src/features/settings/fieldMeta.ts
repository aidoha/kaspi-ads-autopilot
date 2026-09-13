// fieldMeta.ts — подписи и пояснения к полям экрана глобальных настроек.
//
// 14 из 15 полей (все, кроме tacos_window_days) переопределяемы и на уровне
// товара — их тексты уже перенесены дословно в products/fieldMeta.ts
// (тот же словарь field_meta из Jinja-шаблонов, слово в слово). Берём их
// оттуда, а не копируем заново: разошедшиеся дубликаты текста — источник
// молчаливых ошибок при следующей правке формулировки.
//
// tacos_window_days переопределению по товару не подлежит (это окно расчёта
// TACoS для всего биддера), поэтому в products/fieldMeta.ts его нет —
// добавляем здесь, тоже дословно из webui/templates/settings.html.

import { FIELD_META as PRODUCT_FIELD_META } from "../products/fieldMeta";

export const SETTINGS_FIELD_META: Record<string, { label: string; hint: string }> = {
  ...PRODUCT_FIELD_META,
  tacos_window_days: {
    label: "Окно TACoS, дней",
    hint: "За сколько дней считать TACoS для решений биддера. Больше дней = стабильнее, но реакция медленнее; 30 дней заметно нагружает обход Shop API.",
  },
};

/** Подсказки-пресеты для tacos_window_days — как в старой панели
 *  (datalist «2/7/14/30 дней» рядом с полем). */
export const TACOS_WINDOW_PRESETS = [
  { value: 2, label: "2 дня" },
  { value: 7, label: "7 дней" },
  { value: 14, label: "14 дней" },
  { value: 30, label: "30 дней" },
] as const;
