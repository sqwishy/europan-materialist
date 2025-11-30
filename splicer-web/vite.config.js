import { defineConfig } from 'vite';
import solidPlugin from 'vite-plugin-solid';

export default defineConfig({
  plugins: [ solidPlugin() ],
  build: { target: 'esnext' },
  clearScreen: false,
  server: {
      host: '0.0.0.0',
      port: 8011,
			/* by default, if vite can't use the port that you tell it to use,
			 * it will try to use a _different_ port from the one you told it to use...
			 * Apparently, setting strictPort ensures it will do only what you ask,
			 * and not try to do other things that you didn't ask or intend for it to
			 * do as it does by default ... very epic! */
			strictPort: true,
  },
});
