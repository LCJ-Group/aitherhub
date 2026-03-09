import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    '__APP_VERSION__': JSON.stringify({
      app: 'aitherhub-frontend',
      commit: process.env.GIT_COMMIT_SHA || 'unknown',
      branch: process.env.GIT_BRANCH || 'unknown',
      built_at: process.env.BUILD_TIME || 'unknown',
    }),
  },
  build: {
    outDir: "dist",
  },
  server: {
    allowedHosts: [".ngrok-free.app"],
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
