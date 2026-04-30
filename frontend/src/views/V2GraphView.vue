<template>
  <div class="g-shell">
    <header>
      <h1>v2 graph inspector</h1>
      <p class="muted">
        Read-only view of the typed graph that the v2 loader wrote into Neo4j.
        Pick a graph_id, then a label to drill in.
      </p>
    </header>

    <section class="card">
      <h2>1. Graphs</h2>
      <button :disabled="refreshing" @click="refresh">
        {{ refreshing ? 'Loading…' : 'Refresh' }}
      </button>
      <p v-if="error" class="error">{{ error }}</p>
      <table v-if="graphs.length" class="grid">
        <thead>
          <tr>
            <th>graph_id</th>
            <th>per-label counts</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="g in graphs" :key="g.graph_id"
              :class="{ selected: selected === g.graph_id }">
            <td><code>{{ g.graph_id }}</code></td>
            <td>
              <span v-for="(c, lbl) in g.label_counts" :key="lbl" class="pill">
                {{ lbl }}: {{ c }}
              </span>
            </td>
            <td>
              <button @click="select(g.graph_id)">Open</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="!error" class="muted">No graphs found.</p>
    </section>

    <section v-if="selected" class="card">
      <h2>2. Nodes — <code>{{ selected }}</code></h2>
      <div class="row">
        <label>Filter by label
          <select v-model="labelFilter" @change="loadGraph">
            <option value="">(all)</option>
            <option v-for="lbl in labelOptions" :key="lbl" :value="lbl">{{ lbl }}</option>
          </select>
        </label>
        <span class="muted">{{ graph.node_count }} nodes · {{ graph.edge_count }} edges</span>
      </div>

      <table v-if="graph.nodes.length" class="grid">
        <thead>
          <tr>
            <th>label</th>
            <th>key</th>
            <th>name / id</th>
            <th>highlights</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="n in graph.nodes" :key="n.id">
            <td><span class="badge" :data-lbl="n.label">{{ n.label }}</span></td>
            <td><code>{{ n.key }}</code></td>
            <td>{{ n.props.name || n.props.title || n.key }}</td>
            <td class="hl">{{ highlight(n) }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="selected && graph.edges.length" class="card">
      <h2>3. Edges</h2>
      <p class="muted">First 50 edges. Use the JSON endpoint
        <code>GET /api/v2/graphs/{{ selected }}</code> for the complete list.</p>
      <table class="grid">
        <thead>
          <tr>
            <th>type</th>
            <th>source</th>
            <th>→ target</th>
            <th>props</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in graph.edges.slice(0, 50)" :key="e.id">
            <td><span class="badge edge">{{ e.type }}</span></td>
            <td><code>{{ keyOf(e.source) }}</code></td>
            <td><code>{{ keyOf(e.target) }}</code></td>
            <td>{{ formatProps(e.props) }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { getGraph, listGraphs } from '../api/v2'

const graphs = ref([])
const selected = ref('')
const labelFilter = ref('')
const refreshing = ref(false)
const error = ref('')
const graph = ref({ nodes: [], edges: [], node_count: 0, edge_count: 0 })

const labelOptions = computed(() => {
  const g = graphs.value.find(x => x.graph_id === selected.value)
  return g ? Object.keys(g.label_counts).sort() : []
})

const nodeIndex = computed(() => {
  const m = {}
  for (const n of graph.value.nodes) m[n.id] = n
  return m
})

function keyOf(id) {
  const n = nodeIndex.value[id]
  return n ? n.key : id
}

function highlight(n) {
  const p = n.props || {}
  if (n.label === 'Panelist') {
    return [p.age, p.region, p.occupation].filter(Boolean).join(' · ')
  }
  if (n.label === 'Brief') {
    return [p.air_date, p.runtime_minutes && `${p.runtime_minutes}m`, p.channel]
      .filter(Boolean).join(' · ')
  }
  return ''
}

function formatProps(props) {
  const entries = Object.entries(props || {}).filter(([k]) => k !== 'graph_id')
  if (!entries.length) return '—'
  return entries.map(([k, v]) => `${k}=${v}`).join(' ')
}

async function refresh() {
  refreshing.value = true
  error.value = ''
  try {
    const res = await listGraphs()
    graphs.value = res.data || []
  } catch (e) {
    error.value = String(e?.message || e)
  } finally {
    refreshing.value = false
  }
}

async function select(graph_id) {
  selected.value = graph_id
  labelFilter.value = ''
  await loadGraph()
}

async function loadGraph() {
  if (!selected.value) return
  try {
    const opts = labelFilter.value ? { label: labelFilter.value } : {}
    const res = await getGraph(selected.value, opts)
    graph.value = res.data
  } catch (e) {
    error.value = String(e?.message || e)
  }
}

onMounted(refresh)
</script>

<style scoped>
.g-shell { max-width: 1080px; margin: 24px auto; padding: 0 16px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  color: #1f2937; }
header h1 { margin: 0 0 4px; font-size: 24px; }
.muted { color: #6b7280; font-size: 14px; }
.error { color: #b91c1c; font-size: 14px; }
.card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
  padding: 16px 20px; margin: 16px 0; }
.card h2 { margin: 0 0 12px; font-size: 17px; }
.row { display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  margin-bottom: 12px; }
button { border: 1px solid #2563eb; background: #2563eb; color: #fff;
  border-radius: 6px; padding: 6px 14px; font-size: 14px; cursor: pointer; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
table.grid { width: 100%; border-collapse: collapse; font-size: 13px; }
table.grid th, table.grid td {
  text-align: left; padding: 6px 10px; border-bottom: 1px solid #f3f4f6;
  vertical-align: top; }
table.grid tr.selected { background: #eef2ff; }
.pill { display: inline-block; background: #f3f4f6; color: #374151;
  padding: 2px 8px; border-radius: 9999px; font-size: 12px; margin-right: 6px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
  font-size: 11.5px; font-weight: 600; background: #e0e7ff; color: #1e3a8a; }
.badge[data-lbl="Genre"] { background: #ecfccb; color: #3f6212; }
.badge[data-lbl="Slot"] { background: #fef3c7; color: #78350f; }
.badge[data-lbl="Brief"] { background: #fce7f3; color: #831843; }
.badge.edge { background: #f1f5f9; color: #0f172a; font-weight: 500; }
.hl { color: #4b5563; font-size: 12.5px; }
select { border: 1px solid #d1d5db; border-radius: 6px; padding: 4px 8px; }
code { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; }
</style>
