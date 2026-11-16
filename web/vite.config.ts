import { resolve } from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Development: run `PIGLET_TOKEN=dev PIGLET_DEV_ORIGIN=http://localhost:5173 python -m piglet.server`,
// then `VITE_PIGLET_URL=ws://127.0.0.1:<port>/ws VITE_PIGLET_TOKEN=dev npm run dev`.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: resolve(import.meta.dirname, "../src/piglet/server/static"),
    emptyOutDir: true,
    chunkSizeWarningLimit: 1000,
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
