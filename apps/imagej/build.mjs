import { build } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
const root = dirname(fileURLToPath(import.meta.url))
await build({ root, configFile: false, plugins: [vue()], define: { 'process.env.NODE_ENV': '"production"' }, build: {
  outDir: '.build', emptyOutDir: true, copyPublicDir: false, cssCodeSplit: false,
  lib: { entry: join(root, 'frontend/src/entry.ts'), formats: ['es'], fileName: () => 'imagej.js', cssFileName: 'style' },
  rollupOptions: { output: { inlineDynamicImports: true } },
} })
const css = await readFile(join(root, '.build/style.css'), 'utf8')
const js = await readFile(join(root, '.build/imagej.js'), 'utf8')
await mkdir(join(root, 'frontend'), { recursive: true })
await writeFile(join(root, 'frontend/index.js'), `const style=document.createElement('style');style.textContent=${JSON.stringify(css)};document.head.appendChild(style);\n${js}`)
