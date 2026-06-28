import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// The FastAPI app serves the built SPA from app/static (StaticFiles mount in
// codeclash/main.py), so production builds land there directly.
const outDir = fileURLToPath(new URL("../app/static", import.meta.url));

// Dev: Vite on :5173 proxies the real API + websocket to uvicorn on :8000,
// so the frontend talks to the live backend with no CORS or URL juggling.
const BACKEND = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir,
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true },
      "/healthz": { target: BACKEND, changeOrigin: true },
      "/ws": { target: BACKEND, ws: true, changeOrigin: true },
    },
  },
});
