import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  // In dev, the Python server (python app.py) answers /api and /health.
  server: { proxy: { "/api": "http://127.0.0.1:8080", "/health": "http://127.0.0.1:8080" } },
  build: { outDir: "dist", emptyOutDir: true },
});
