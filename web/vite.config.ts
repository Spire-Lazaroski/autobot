import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Ports live in the repo-root .env (API_PORT, WEB_PORT). Only the port numbers are read
// here; nothing from .env is exposed to the browser (no VITE_ prefixed variables).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "..", "");
  const api = `127.0.0.1:${env.API_PORT || "8710"}`;
  return {
    plugins: [react(), tailwindcss()],
    server: {
      host: "127.0.0.1",
      port: Number(env.WEB_PORT || 5190),
      strictPort: true,
      proxy: {
        "/api": { target: `http://${api}`, changeOrigin: true },
        "/ws": { target: `ws://${api}`, ws: true },
      },
    },
  };
});
