/**
 * Extension → CodeMirror language, loaded on demand.
 *
 * Every mode is a literal dynamic import so esbuild's code splitting keeps
 * each grammar in its own chunk — opening a JSON file does not fetch the
 * C++ grammar. R, Julia, shell and friends have no CodeMirror 6 grammar of
 * their own and come from the legacy stream parsers, which is plenty.
 */

async function stream(load) {
  const { StreamLanguage } = await import('@codemirror/language')
  const modes = await load()
  // Each legacy module exports one or more parsers; the first is the one
  // named after the file in every case we use here.
  return StreamLanguage.define(Object.values(modes)[0])
}

const LOADERS = {
  '.py': async () => (await import('@codemirror/lang-python')).python(),
  '.pyi': async () => (await import('@codemirror/lang-python')).python(),
  '.ipynb': async () => (await import('@codemirror/lang-json')).json(),

  '.js': async () => (await import('@codemirror/lang-javascript')).javascript(),
  '.mjs': async () => (await import('@codemirror/lang-javascript')).javascript(),
  '.jsx': async () => (await import('@codemirror/lang-javascript')).javascript({ jsx: true }),
  '.ts': async () => (await import('@codemirror/lang-javascript')).javascript({ typescript: true }),
  '.tsx': async () =>
    (await import('@codemirror/lang-javascript')).javascript({ typescript: true, jsx: true }),

  '.json': async () => (await import('@codemirror/lang-json')).json(),
  '.md': async () => (await import('@codemirror/lang-markdown')).markdown(),
  '.markdown': async () => (await import('@codemirror/lang-markdown')).markdown(),
  '.html': async () => (await import('@codemirror/lang-html')).html(),
  '.vue': async () => (await import('@codemirror/lang-html')).html(),
  '.css': async () => (await import('@codemirror/lang-css')).css(),
  '.xml': async () => (await import('@codemirror/lang-xml')).xml(),
  '.svg': async () => (await import('@codemirror/lang-xml')).xml(),
  '.yaml': async () => (await import('@codemirror/lang-yaml')).yaml(),
  '.yml': async () => (await import('@codemirror/lang-yaml')).yaml(),
  '.sql': async () => (await import('@codemirror/lang-sql')).sql(),
  '.rs': async () => (await import('@codemirror/lang-rust')).rust(),
  '.c': async () => (await import('@codemirror/lang-cpp')).cpp(),
  '.h': async () => (await import('@codemirror/lang-cpp')).cpp(),
  '.cpp': async () => (await import('@codemirror/lang-cpp')).cpp(),
  '.hpp': async () => (await import('@codemirror/lang-cpp')).cpp(),

  // Stream parsers — enough for reading, and the only option for these.
  '.r': () => stream(() => import('@codemirror/legacy-modes/mode/r')),
  '.jl': () => stream(() => import('@codemirror/legacy-modes/mode/julia')),
  '.sh': () => stream(() => import('@codemirror/legacy-modes/mode/shell')),
  '.bash': () => stream(() => import('@codemirror/legacy-modes/mode/shell')),
  '.zsh': () => stream(() => import('@codemirror/legacy-modes/mode/shell')),
  '.toml': () => stream(() => import('@codemirror/legacy-modes/mode/toml')),
  '.ini': () => stream(() => import('@codemirror/legacy-modes/mode/properties')),
  '.cfg': () => stream(() => import('@codemirror/legacy-modes/mode/properties')),
  '.env': () => stream(() => import('@codemirror/legacy-modes/mode/properties')),
  '.go': () => stream(() => import('@codemirror/legacy-modes/mode/go')),
  '.java': () => stream(() => import('@codemirror/legacy-modes/mode/clike')),
  '.lua': () => stream(() => import('@codemirror/legacy-modes/mode/lua')),
  '.dockerfile': () => stream(() => import('@codemirror/legacy-modes/mode/dockerfile')),
}

/** Filenames that carry their language in the name, not an extension. */
const BY_NAME = {
  dockerfile: () => stream(() => import('@codemirror/legacy-modes/mode/dockerfile')),
  makefile: () => stream(() => import('@codemirror/legacy-modes/mode/cmake')),
  'cmakelists.txt': () => stream(() => import('@codemirror/legacy-modes/mode/cmake')),
}

export async function languageFor(fileName, ext) {
  const loader = BY_NAME[fileName.toLowerCase()] ?? LOADERS[ext]
  if (!loader) return null
  try {
    return await loader()
  } catch (e) {
    console.warn('[text-viewer] no highlighting for', ext, e)
    return null
  }
}
