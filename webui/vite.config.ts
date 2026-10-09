import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 产物要能被 QWebEngineView 用 file:// 直接打开（形态 A），所以：
//   ① base 用相对路径 './'，否则 file:// 下资源 404；
//   ② 打成**单文件 IIFE**（inlineDynamicImports + format: 'iife'），
//      因为 file:// 下 ES module 会被 Chromium 的 CORS 规则拦掉（Not allowed to load local resource）。
//   ③ 不做 CSS 拆分，产物固定为 app.js + app.css，方便 PyInstaller 打包与人工核对。
export default defineConfig({
  base: './',
  plugins: [vue()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'chrome100',
    cssCodeSplit: false,
    assetsInlineLimit: 4096,
    rollupOptions: {
      output: {
        format: 'iife',
        inlineDynamicImports: true,
        entryFileNames: 'app.js',
        assetFileNames: 'app.[ext]',
      },
    },
  },
  server: {
    // 试点固定端口，方便与后端/文档对齐（仅开发用，不参与打包）
    port: 5180,
    strictPort: true,
  },
})
