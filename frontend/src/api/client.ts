export class ApiError extends Error {
  constructor(readonly status: number, readonly errors: string[]) {
    super(errors[0] ?? `Ошибка ${status}`);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    // credentials: кука сессии — единственная авторизация. Токенов нет
    // сознательно: токен в localStorage унесла бы любая XSS.
    credentials: "include",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    // API всегда отвечает {"errors": [...]} — одна форма на все ошибки.
    let errors: string[] = [`Ошибка ${res.status}`];
    try {
      const data = await res.json();
      if (Array.isArray(data?.errors) && data.errors.length) errors = data.errors;
    } catch { /* тело не JSON — оставляем текст по коду */ }
    throw new ApiError(res.status, errors);
  }
  return (await res.json()) as T;
}

export const apiGet = <T,>(path: string) => request<T>("GET", path);
export const apiSend = <T,>(method: "POST" | "PUT", path: string, body?: unknown) =>
  request<T>(method, path, body);
