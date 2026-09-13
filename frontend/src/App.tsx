import { useEffect, useState } from "react";
import { apiGet, apiSend, ApiError } from "./api/client";
import LoginScreen from "./features/auth/LoginScreen";

type AuthState =
  | { kind: "loading" }
  | { kind: "anon" }
  | { kind: "user"; user: string };

/** Оболочка приложения. На старте зовёт GET /api/me: 401 → экран входа,
 *  успех → главный экран (пока заглушка — следующие задачи достроят его). */
export default function App() {
  const [auth, setAuth] = useState<AuthState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    apiGet<{ user: string }>("/api/me")
      .then((res) => {
        if (!cancelled) setAuth({ kind: "user", user: res.user });
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          setAuth({ kind: "anon" });
        } else {
          // Неожиданная ошибка (сеть, 5xx) — тоже показываем вход: у
          // пользователя нет другого способа восстановить сессию.
          setAuth({ kind: "anon" });
        }
      });
    return () => { cancelled = true; };
  }, []);

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

  if (auth.kind === "anon") {
    return <LoginScreen onLoggedIn={(user) => setAuth({ kind: "user", user })} />;
  }

  return (
    <div style={{ padding: 24 }}>
      <p>
        Вы вошли как <strong>{auth.user}</strong>.
      </p>
      <button type="button" className="btn" onClick={logout}>Выйти</button>
    </div>
  );
}
