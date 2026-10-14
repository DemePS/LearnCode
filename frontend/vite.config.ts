import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // monacoSetup.ts builds its workers with `new Worker(url, { type: "module" })`, and an ES-module
  // worker cannot be bundled as the classic script Vite emits by default. Without this, the worker
  // fails to start, and Monaco silently loses its language services -- no syntax colours, no bracket
  // matching, no diagnostics -- rather than reporting anything.
  worker: {
    format: "es",
  },
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
