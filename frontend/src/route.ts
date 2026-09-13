// route.ts — чистая логика навигации на два экрана (товары/настройки).
// Полноценный роутер не нужен: экранов всего два, состояния в App
// достаточно. Адрес в браузере всё равно должен меняться (history.pushState)
// — иначе перезагрузка страницы теряет открытый экран и возвращает на
// главную. Отдача SPA уже умеет открывать любой путь (см. task-1).

export type Screen = "products" | "settings";

/** Путь из адресной строки → экран, который надо показать при загрузке
 *  или по popstate (кнопка «назад» в браузере). */
export function screenFromPath(path: string): Screen {
  return path === "/settings" || path.startsWith("/settings/") ? "settings" : "products";
}

/** Экран → путь для history.pushState. */
export function pathForScreen(screen: Screen): string {
  return screen === "settings" ? "/settings" : "/";
}
