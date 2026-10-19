export class ApiError extends Error {
  status: number;
  errors: string[];
  constructor(status: number, errors: string[]) {
    super(errors[0] ?? `Ошибка ${status}`);
    this.status = status;
    this.errors = errors;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  // Файл уходит сырым телом, как есть: JSON.stringify превратил бы его в «{}».
  const raw = body instanceof Blob;
  const res = await fetch(path, {
    method,
    // credentials: кука сессии — единственная авторизация. Токенов нет
    // сознательно: токен в localStorage унесла бы любая XSS.
    credentials: "include",
    headers: body === undefined ? {}
      : { "Content-Type": raw ? "application/octet-stream" : "application/json" },
    body: body === undefined ? undefined : raw ? body : JSON.stringify(body),
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
export const apiSend = <T,>(method: "POST" | "PUT" | "DELETE", path: string, body?: unknown) =>
  request<T>(method, path, body);
