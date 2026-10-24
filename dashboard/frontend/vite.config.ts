import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  preview: { port: 5192, strictPort: true },
  server: {
    port: 5191,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8744",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
