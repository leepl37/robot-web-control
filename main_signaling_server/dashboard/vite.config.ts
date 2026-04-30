import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/static/dashboard/',
  build: {
    outDir: '../static/dashboard',
    emptyOutDir: true,
  },
});
