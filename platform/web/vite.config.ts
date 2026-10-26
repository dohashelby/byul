import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발 서버(npm run dev)는 /api를 FastAPI(127.0.0.1:8700)로 넘긴다. 운영에서는 FastAPI가 dist를 직접 서빙한다.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: { "/api": "http://127.0.0.1:8700" },
    // 테스트가 ../../contracts/examples를 픽스처로 읽는다.
    fs: { allow: [".", "../../contracts"] },
  },
  // 루프백 전용 화면이라 한 묶음(약 900 kB)으로 둔다.
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1000 },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    css: { modules: { classNameStrategy: "non-scoped" } },
  },
});
