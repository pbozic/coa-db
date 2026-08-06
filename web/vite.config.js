import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Builds into the same output/site folder the Python pipeline writes assets to,
// so `python icons.py` and `npm run build` land side by side.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: { outDir: '../output/site', emptyOutDir: false },
})
