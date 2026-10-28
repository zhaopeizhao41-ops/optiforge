import { defineConfig } from 'vite';

export default defineConfig({
  base: './',
  server: { proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true } } },
  build: {
    modulePreload: false,
    sourcemap: false,
    rolldownOptions: {
      onwarn(warning, defaultHandler) {
        // This is a client-only SPA. RSC directives in dependencies have no
        // meaning here; retain all other bundler diagnostics.
        if (warning.code === 'MODULE_LEVEL_DIRECTIVE' && warning.message.includes('use client')) return;
        defaultHandler(warning);
      },
    },
  },
});
