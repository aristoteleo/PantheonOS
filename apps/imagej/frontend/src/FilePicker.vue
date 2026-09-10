<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { AppHost } from './host'
const props = defineProps<{ host: AppHost; accept: string[]; title: string }>()
const emit = defineEmits<{ pick: [path: string]; close: [] }>()
const directory = ref('')
const draft = ref('')
const entries = ref<Array<{ name: string; type: string }>>([])
const loading = ref(false)
const error = ref('')
const visible = computed(() => entries.value.filter(e => e.type === 'directory' || props.accept.some(ext => e.name.toLowerCase().endsWith(ext))))
let generation = 0
async function browse(path: string) {
  const token = ++generation
  loading.value = true
  error.value = ''
  try {
    const found = await props.host.fs.ls(path)
    if (token !== generation) return
    directory.value = path
    draft.value = path
    entries.value = found
  } catch (cause) { if (token === generation) error.value = String(cause) }
  finally { if (token === generation) loading.value = false }
}
function choose(entry: { name: string; type: string }) {
  const path = [directory.value.replace(/\/$/, ''), entry.name].filter(Boolean).join('/')
  if (entry.type === 'directory') void browse(path)
  else emit('pick', path)
}
onMounted(() => browse(''))
</script>
<template>
  <div class="shade" @click.self="emit('close')">
    <section role="dialog" aria-modal="true" :aria-label="title" @keydown.esc="emit('close')">
      <header><strong>{{ title }}</strong><button aria-label="Close file picker" @click="emit('close')">×</button></header>
      <form @submit.prevent="browse(draft)"><button type="button" :disabled="!directory" aria-label="Parent folder" @click="browse(directory.split('/').slice(0, -1).join('/'))">↑</button><input v-model="draft" aria-label="Folder path" placeholder="Workspace" /><button>Open folder</button></form>
      <p v-if="error" role="alert">{{ error }}</p>
      <div class="files" :aria-busy="loading"><p v-if="loading">Loading…</p><button v-for="entry in visible" :key="entry.name" @click="choose(entry)">{{ entry.type === 'directory' ? '▸' : '·' }} {{ entry.name }}</button><p v-if="!loading && !visible.length">No supported images in this folder.</p></div>
    </section>
  </div>
</template>
<style scoped>
.shade { position:absolute; inset:0; z-index:5; background:#0005; display:grid; place-items:center; padding:20px; }
section { background:var(--surface,#fff); color:var(--text,#222); border:1px solid var(--border,#8885); border-radius:10px; width:min(520px,100%); max-height:90%; display:flex; flex-direction:column; padding:12px; gap:10px; box-sizing:border-box; }
header, form { display:flex; align-items:center; gap:8px; } header strong { flex:1; } input { min-width:0; flex:1; }
button,input { font:inherit; color:inherit; border:1px solid var(--border,#8885); border-radius:5px; padding:6px 8px; background:transparent; } button { cursor:pointer; }
.files { overflow:auto; min-height:100px; } .files button { display:block; text-align:left; width:100%; border:0; } .files button:hover { background:#8882; }
p { font-size:12px; } [role=alert] { color:#b54e30; }
</style>
