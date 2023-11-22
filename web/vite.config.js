import { defineConfig } from 'vite';
import solidPlugin from 'vite-plugin-solid';

export default defineConfig({
  plugins: [ solidPlugin() ],
  build: { target: 'esnext' },
  clearScreen: false,
  server: {
      host: '0.0.0.0',
      port: 8011,
  },
});
