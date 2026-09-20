import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "./",
  build: {
    outDir: "dist/client",
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    host: "0.0.0.0",
    allowedHosts: ["terminal.local"],
    proxy: {
      "/dota2/api": {
        // Nginx strips the /dota2/api prefix in production, so the dev proxy
        // must strip it too. Override the origin with DOTA2_API_PROXY.
        target: process.env.DOTA2_API_PROXY || "https://ashfury.cn",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/dota2\/api/, ""),
      },
    },
    warmup: {
      clientFiles: ["./src/main.jsx"],
    },
  },
  plugins: [react()],
});
