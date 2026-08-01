import { resolve } from 'node:path';
import { defineConfig, externalizeDepsPlugin } from 'electron-vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      lib: { entry: resolve(__dirname, 'electron/main/index.ts') },
      rollupOptions: { output: { format: 'es' } },
    },
    resolve: {
      alias: { '@shared': resolve(__dirname, 'electron/shared') },
    },
  },

  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      lib: { entry: resolve(__dirname, 'electron/preload/index.ts') },
      // preload 必须是 cjs：contextIsolation 下 ESM preload 在部分场景加载不了
      rollupOptions: { output: { format: 'cjs' } },
    },
  },

  renderer: {
    root: resolve(__dirname, 'src'),
    plugins: [react()],
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src'),
        '@shared': resolve(__dirname, 'electron/shared'),
      },
    },
    build: {
      rollupOptions: {
        input: { index: resolve(__dirname, 'src/index.html') },
      },
      // 字体分片有 202 个，别因为体积告警刷屏
      chunkSizeWarningLimit: 1500,
      assetsInlineLimit: 0,
    },
    server: { port: 5273, strictPort: true },
  },
});
