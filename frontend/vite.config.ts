import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Дев-сервер Vite отдаёт фронт, а всё под /api проксирует на бэкенд.
// Так браузер видит один origin, и кука сессии (SameSite=strict) доезжает —
// без прокси она бы просто не отправлялась, и логин не работал бы локально.
//
// По умолчанию бэкенд локальный (./run_webui_local.sh). Чтобы локальным фронтом
// дёргать БОЕВУЮ панель — `npm run dev:prod` (или API_TARGET=... npm run dev).
// Внимание: прод крутится с dry_run:false, любой PUT оттуда меняет живые ставки.
const API_TARGET = process.env.API_TARGET || "http://127.0.0.1:8000";
const IS_LOCAL = /127\.0\.0\.1|localhost/.test(API_TARGET);

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: API_TARGET,
        // Caddy на VPS матчит сайт по адресу-IP, т.е. по заголовку Host. Без
        // подмены туда уехал бы Host: localhost:5173 и Caddy не нашёл бы сайт.
        // Локальному uvicorn подмена не нужна и только путает логи.
        changeOrigin: !IS_LOCAL,
        // Сертификат панели самоподписанный (`tls internal`: hostname
        // cloud-001.h-161398.kz публично не резолвится), Node иначе рвёт
        // соединение на проверке цепочки.
        secure: IS_LOCAL,
      },
    },
  },
  build: { outDir: "../webui/static/dist", emptyOutDir: true },
});
