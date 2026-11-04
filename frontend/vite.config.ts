/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const API_URL = process.env.EVALFORGE_API_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Même origine que l'API pour le navigateur : le cookie de session reste limité à /api/v1/auth.
    proxy: { '/api': API_URL },
  },
  preview: { proxy: { '/api': API_URL } },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
