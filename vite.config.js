import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// /api → Lumen 后端（python -m lumen）。只监听本机：后端持有平台密钥，不要把开发服务器暴露到局域网。
const API = process.env.LUMEN_API || 'http://127.0.0.1:8787'
const proxy = { '/api': { target: API, changeOrigin: true } }

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5180, proxy },
  preview: { host: '127.0.0.1', port: 5180, proxy },
})
