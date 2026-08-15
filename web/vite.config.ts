import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

// 前端开发服务器：/api 代理到后端 8000
export default defineConfig({
  base: './',
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('/antd/') || id.includes('/@ant-design/')) return 'vendor-antd'
          if (id.includes('/echarts') || id.includes('/zrender/')) return 'vendor-charts'
          if (id.includes('/@antv/') || id.includes('/react-force-graph')) return 'vendor-graph'
          if (id.includes('/react-markdown/') || id.includes('/remark-') || id.includes('/rehype-')) return 'vendor-markdown'
          return undefined
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
