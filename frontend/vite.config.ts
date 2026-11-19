import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// `./start.sh dev` passes the backend port as API_PORT; plain `pnpm dev` assumes 8000.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: { proxy: { '/api': `http://127.0.0.1:${loadEnv(mode, '.', 'API_').API_PORT ?? 8000}` } },
}))
