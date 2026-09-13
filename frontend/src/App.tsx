import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend, ApiError } from "./api/client";
import LoginScreen from "./features/auth/LoginScreen";

type AuthState =
  | { kind: "loading" }
  | { kind: "anon" }
  | { kind: "error"; message: string }
  | { kind: "user"; user: string };

/** Оболочка приложения. На старте зовёт GET /api/me: 401 → экран входа,
 *  успех → главный экран (пока заглушка — следующие задачи достроят его).
 *
 *  401 и «сервер недоступен» — разные ситуации и не должны выглядеть
 *  одинаково: упавший бэкенд не значит «вы не вошли», и владелец не должен
 *  вводить верный пароль в ответ на сетевую ошибку, думая, что забыл его. */
export default function App() {
  const [auth, setAuth] = useState<AuthState>({ kind: "loading" });

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

  return (
    <div style={{ padding: 24 }}>
      <p>
        Вы вошли как <strong>{auth.user}</strong>.
      </p>
      <button type="button" className="btn" onClick={logout}>Выйти</button>
    </div>
  );
}
