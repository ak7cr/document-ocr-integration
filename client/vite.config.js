import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
export default defineConfig({ root: fileURLToPath(new URL('.', import.meta.url)), plugins: [react(), tailwindcss()], server: { port: 5173, proxy: { '/api': 'http://localhost:3001' } } });
