import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Дев-сервер Vite отдаёт фронт, а всё под /api проксирует на uvicorn.
// Так браузер видит один origin, и кука сессии (SameSite=strict) доезжает —
// без прокси она бы просто не отправлялась, и логин не работал бы локально.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: { outDir: "../webui/static/dist", emptyOutDir: true },
});
