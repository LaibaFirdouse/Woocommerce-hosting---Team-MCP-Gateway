import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Vite, Vitest, and @vitejs/plugin-react share one Vite 5.4.21 install
// (see package.json overrides) so PluginOption types align.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    globals: true,
    pool: "threads",
    poolOptions: {
      threads: {
        singleThread: true,
        isolate: false,
      },
    },
    fileParallelism: false,
  },
});
