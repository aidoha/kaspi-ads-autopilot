// route.ts — чистая логика навигации по экранам (товары/настройки/отчёты).
// Полноценный роутер не нужен: экранов всего три, состояния в App
// достаточно. Адрес в браузере всё равно должен меняться (history.pushState)
// — иначе перезагрузка страницы теряет открытый экран и возвращает на
// главную. Отдача SPA уже умеет открывать любой путь (см. task-1).

export type Screen = "products" | "settings" | "reports";

/** Путь из адресной строки → экран, который надо показать при загрузке
 *  или по popstate (кнопка «назад» в браузере). */
export function screenFromPath(path: string): Screen {
  if (path === "/settings" || path.startsWith("/settings/")) return "settings";
  if (path === "/reports" || path.startsWith("/reports/")) return "reports";
  return "products";
}

/** Экран → путь для history.pushState. */
export function pathForScreen(screen: Screen): string {
  if (screen === "settings") return "/settings";
  if (screen === "reports") return "/reports";
  return "/";
}
