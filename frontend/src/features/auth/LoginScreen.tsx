import { useState } from "react";
import { apiSend, ApiError } from "../../api/client";

type Props = {
  /** Вызывается после успешного входа с именем пользователя из ответа API. */
  onLoggedIn: (user: string) => void;
};

/** Экран входа. Разметка и стили — язык макета (.card/.btn), т.к. отдельного
 *  экрана входа в утверждённом макете нет. Ошибку показываем текстом из
 *  ApiError.errors, а не общей фразой — пользователь должен видеть, что
 *  именно не так (неверный пароль? сервер недоступен?). */
export default function LoginScreen({ onLoggedIn }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await apiSend<{ user: string }>("POST", "/api/login", { username, password });
      onLoggedIn(res.user);
    } catch (err) {
      setError(err instanceof ApiError ? err.errors.join(", ") : "Не удалось связаться с сервером");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", justifyContent: "center", padding: "80px 24px" }}>
      <form className="card" style={{ width: 340 }} onSubmit={submit}>
        <h1 style={{ fontSize: 19, fontWeight: 620, letterSpacing: "-0.015em", margin: "0 0 18px" }}>
          Автопилот ставок
        </h1>

        <div className="field-text">
          <label htmlFor="username">Логин</label>
          <input
            id="username" name="username" type="text" autoComplete="username"
            required autoFocus value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </div>

        <div className="field-text">
          <label htmlFor="password">Пароль</label>
          <input
            id="password" name="password" type="password" autoComplete="current-password"
            required value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        {error && (
          <p role="alert" style={{ color: "var(--crit)", fontSize: 13, marginTop: 0, marginBottom: 14 }}>
            {error}
          </p>
        )}

        <button type="submit" className="btn btn-primary" style={{ width: "100%" }} disabled={busy}>
          {busy ? "Вход…" : "Войти"}
        </button>
      </form>
    </div>
  );
}
