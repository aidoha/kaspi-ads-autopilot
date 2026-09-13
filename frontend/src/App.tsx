import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend, ApiError } from "./api/client";
import LoginScreen from "./features/auth/LoginScreen";
import ProductsScreen from "./features/products/ProductsScreen";
import SettingsScreen from "./features/settings/SettingsScreen";
import { pathForScreen, screenFromPath, type Screen } from "./route";

type AuthState =
  | { kind: "loading" }
  | { kind: "anon" }
  | { kind: "error"; message: string }
  | { kind: "user"; user: string };

/** Оболочка приложения. На старте зовёт GET /api/me: 401 → экран входа,
 *  успех → главный экран (список товаров, ProductsScreen).
 *
 *  401 и «сервер недоступен» — разные ситуации и не должны выглядеть
 *  одинаково: упавший бэкенд не значит «вы не вошли», и владелец не должен
 *  вводить верный пароль в ответ на сетевую ошибку, думая, что забыл его. */
export default function App() {
  const [auth, setAuth] = useState<AuthState>({ kind: "loading" });
  const [screen, setScreen] = useState<Screen>(() => screenFromPath(window.location.pathname));
  // Тестовый режим — общее состояние приложения: индикатор в шапке главного
  // экрана и тоггл в настройках обязаны показывать одно и то же, а не
  // расходиться после переключения (see task-5-brief, шаг 2). null, пока
  // ещё не узнали текущее значение.
  const [dryRun, setDryRun] = useState<boolean | null>(null);

  const checkMe = useCallback(() => {
    setAuth({ kind: "loading" });
    apiGet<{ user: string }>("/api/me")
      .then((res) => {
        setAuth({ kind: "user", user: res.user });
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setAuth({ kind: "anon" });
        } else if (err instanceof ApiError) {
          setAuth({ kind: "error", message: err.errors.join(", ") });
        } else {
          // fetch бросил до того, как дело дошло до ApiError — сеть недоступна.
          setAuth({ kind: "error", message: "Панель не может связаться с сервером" });
        }
      });
  }, []);

  useEffect(() => {
    checkMe();
  }, [checkMe]);

  // Кнопка «назад» в браузере — путь меняем сами через navigate(), но
  // адрес может смениться и снаружи (пользователь дёрнул назад/вперёд).
  useEffect(() => {
    function onPopState() {
      setScreen(screenFromPath(window.location.pathname));
    }
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  // Индикатор режима в шапке должен быть верным ещё до того, как владелец
  // вообще открыл настройки — подгружаем один раз при входе, не дожидаясь
  // перехода на экран настроек.
  useEffect(() => {
    if (auth.kind !== "user") return;
    apiGet<{ settings: { dry_run: boolean } }>("/api/settings")
      .then((res) => setDryRun(Boolean(res.settings.dry_run)))
      .catch(() => { /* индикатор необязателен — экран товаров им не блокируем */ });
  }, [auth.kind]);

  function navigate(next: Screen) {
    setScreen(next);
    const path = pathForScreen(next);
    if (window.location.pathname !== path) {
      window.history.pushState(null, "", path);
    }
  }

  async function logout() {
    try {
      await apiSend("POST", "/api/logout");
    } finally {
      setAuth({ kind: "anon" });
    }
  }

  if (auth.kind === "loading") {
    return (
      <div style={{ padding: 48, textAlign: "center", color: "var(--ink-2)" }}>
        Загрузка…
      </div>
    );
  }

  if (auth.kind === "error") {
    return (
      <div style={{ padding: 48, textAlign: "center" }}>
        <p style={{ color: "var(--crit)" }}>{auth.message}</p>
        <button type="button" className="btn" onClick={checkMe}>Повторить</button>
      </div>
    );
  }

  if (auth.kind === "anon") {
    return <LoginScreen onLoggedIn={(user) => setAuth({ kind: "user", user })} />;
  }

  if (screen === "settings") {
    return (
      <SettingsScreen
        dryRun={dryRun}
        onDryRunChange={setDryRun}
        onBack={() => navigate("products")}
        onLogout={logout}
      />
    );
  }

  return (
    <ProductsScreen
      onLogout={logout}
      dryRun={dryRun}
      onOpenSettings={() => navigate("settings")}
    />
  );
}
