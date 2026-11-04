import { defineConfig } from 'cypress'

export default defineConfig({
  e2e: {
    baseUrl: 'http://127.0.0.1:5173',
    // La fenêtre du navigateur sans affichage fait 1 280 px : une vue plus large serait coupée sur les captures.
    viewportWidth: 1280,
    viewportHeight: 800,
    defaultCommandTimeout: 10_000,
    video: false,
    screenshotOnRunFailure: true,
  },
  expose: {
    // Les captures du README ne sont prises qu'avec EVALFORGE_SCREENSHOTS=1.
    screenshots: process.env.EVALFORGE_SCREENSHOTS === '1',
  },
})
