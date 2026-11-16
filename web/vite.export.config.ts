import { resolve } from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The HTML export viewer: one JS file and one CSS file, which the server inlines into a
// standalone page. It shares the transcript components but not the live client.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: resolve(import.meta.dirname, "../src/piglet/server/static/export"),
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(import.meta.dirname, "export.html"),
      output: { codeSplitting: false },
    },
  },
});
