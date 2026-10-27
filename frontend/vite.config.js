import { defineConfig } from 'vite';

export default defineConfig({
  base: './',
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
