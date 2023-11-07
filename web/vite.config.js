import { defineConfig } from 'vite';
import solidPlugin from 'vite-plugin-solid';
// import Icons from 'unplugin-icons/vite'

export default defineConfig({
    plugins: [
      solidPlugin(),
      // Icons({ compiler: 'solid' }),
    ],
    build: {
        target: 'esnext',
        // polyfillDynamicImport: false,
        // rollupOptions: {
        //     /* I don't remember why I did this but it fucks up `vite build` */
        //   external: [/*"styles.css",*/ "index.html"],
        // }
    },
    clearScreen: false,
    server: {
        host: '0.0.0.0',
        port: 8011,
    },
});
