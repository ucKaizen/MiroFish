<template>
  <div class="v2-shell">
    <header class="v2-header">
      <h1>MiroFish v2 — schema-direct run</h1>
      <p class="muted">
        No-fork, no-LLM ingestion. Schema → Neo4j → engagement gate → LLM
        reactions → deterministic metrics → thin narrator.
      </p>
    </header>

    <section class="card">
      <h2>1. Studies</h2>

      <div class="row">
        <label class="file-pick">
          <input type="file" accept=".zip,.json" @change="onFilePicked" />
          <span>Choose study (.zip with study.json + CSVs, or bare study.json)</span>
        </label>
        <button :disabled="!pickedFile || uploading" @click="uploadPicked">
          {{ uploading ? 'Uploading…' : 'Upload study' }}
        </button>
      </div>
      <p v-if="pickedFile" class="muted small">Picked: {{ pickedFile.name }} ({{ humanSize(pickedFile.size) }})</p>
      <p v-if="uploadError" class="error">{{ uploadError }}</p>
      <p v-if="uploadOk" class="ok small">
        Uploaded <code>{{ uploadOk.study_id }}</code> at {{ uploadOk.registered_at }}
        <span v-if="uploadOk.suffixed">
          (the file's study_id collided with an existing entry, so it was
          registered under a suffixed id — both rows are now selectable)
        </span>
      </p>

      <details class="from-disk">
        <summary>or register a server-side path</summary>
        <div class="row">
          <input
            v-model="newStudyPath"
            type="text"
            placeholder="seeds/bbc_panel/study.json"
            class="grow"
          />
          <button :disabled="!newStudyPath || registering" @click="registerStudy">
            {{ registering ? 'Registering…' : 'Register from path' }}
          </button>
        </div>
        <p v-if="registerError" class="error">{{ registerError }}</p>
      </details>

      <table v-if="studies.length" class="grid">
        <thead>
          <tr>
            <th></th>
            <th>study_id</th>
            <th>name</th>
            <th>panelists</th>
            <th>edges</th>
            <th>brief</th>
            <th>registered</th>
            <th>files</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in studies" :key="s.study_id"
              :class="{ selected: selectedStudyId === s.study_id }"
              @click="selectedStudyId = s.study_id">
            <td>
              <input type="radio"
                     :value="s.study_id"
                     v-model="selectedStudyId"
                     :name="'pick-study'" />
            </td>
            <td><code>{{ s.study_id }}</code></td>
            <td>{{ s.name }}</td>
            <td>{{ s.panelists }}</td>
            <td>{{ s.edges }}</td>
            <td>{{ s.brief.title }} ({{ s.brief.air_date }})</td>
            <td class="muted small">{{ s.registered_at || '—' }}</td>
            <td class="file-cell">
              <a :href="`/api/v2/studies/${s.study_id}/json`"
                 :download="`${s.study_id}.json`"
                 class="link"
                 @click.stop>study.json</a>
              <a :href="`/api/v2/studies/${s.study_id}/bundle`"
                 :download="`${s.study_id}.zip`"
                 class="link"
                 @click.stop>full .zip</a>
            </td>
            <td>
              <button class="ghost danger"
                      :disabled="deletingId === s.study_id"
                      @click.stop="onDeleteStudy(s)">
                {{ deletingId === s.study_id ? 'Deleting…' : 'Delete' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">No studies registered yet.</p>
    </section>

    <section class="card">
      <h2>2. Run</h2>
      <p v-if="selectedStudy" class="selection">
        Selected: <strong>{{ selectedStudy.name }}</strong>
        — <code>{{ selectedStudy.study_id }}</code>
        ({{ selectedStudy.panelists }} panelists, {{ selectedStudy.edges }} edges)
      </p>
      <p v-else class="muted">Pick a study above (radio in the table).</p>
      <div class="row">
        <label>Rounds
          <input v-model.number="rounds" type="number" min="1" max="5" class="narrow" />
        </label>
        <label class="checkbox">
          <input v-model="skipNeo4j" type="checkbox" /> skip Neo4j
        </label>
        <label class="checkbox">
          <input v-model="noLlmNarrator" type="checkbox" /> deterministic narrator (no LLM)
        </label>
        <button :disabled="!selectedStudyId || running" @click="kickOffRun">
          {{ running ? 'Running…' : 'Run simulation' }}
        </button>
      </div>
      <p v-if="runError" class="error">{{ runError }}</p>
    </section>

    <section v-if="activeRun" class="card">
      <h2>3. Run status — <code>{{ activeRun.run_id }}</code></h2>
      <p>
        <strong>{{ activeRun.status }}</strong>
        — step {{ activeRun.step }} / {{ activeRun.step_total }}
        <span class="downloads">
          <a :href="`/api/v2/studies/${activeRun.study_id}/json`"
             :download="`${activeRun.study_id}.json`"
             class="link">study.json</a>
          <a :href="`/api/v2/studies/${activeRun.study_id}/bundle`"
             :download="`${activeRun.study_id}.zip`"
             class="link">full bundle .zip</a>
        </span>
      </p>
      <pre class="log">{{ logText }}</pre>
      <div v-if="activeRun.headline" class="headline">
        <span><strong>reach</strong> {{ activeRun.headline.reach }}/{{ activeRun.headline.panel_size }}</span>
        <span><strong>engagement</strong> {{ activeRun.headline.engagement }}/{{ activeRun.headline.panel_size }}</span>
        <span><strong>AI</strong> {{ aiStr(activeRun.headline.appreciation_index) }}</span>
        <span><strong>clarity risk</strong> {{ activeRun.headline.clarity_risk }}/{{ activeRun.headline.panel_size }}</span>
      </div>
    </section>

    <section v-if="graphData" class="card">
      <h2>4. Graph</h2>
      <p class="muted">
        Typed graph that the v2 loader wrote into Neo4j for this study.
        {{ graphData.node_count }} nodes, {{ graphData.edge_count }} edges.
        Click any node to see its full attributes.
      </p>
      <div class="graph-legend">
        <span class="legend-pill" data-lbl="Panelist">Panelist</span>
        <span class="legend-pill" data-lbl="Genre">Genre</span>
        <span class="legend-pill" data-lbl="Slot">Slot</span>
        <span class="legend-pill" data-lbl="Brief">Brief</span>
      </div>
      <div class="graph-flex">
        <svg ref="graphSvg" class="graph-svg" :width="graphWidth" :height="graphHeight"></svg>
        <aside class="node-panel">
          <div v-if="!selectedNode" class="muted small">
            Click a node to inspect.
          </div>
          <div v-else>
            <div class="node-head">
              <span class="legend-pill" :data-lbl="selectedNode.label">{{ selectedNode.label }}</span>
              <code>{{ selectedNode.key }}</code>
            </div>
            <h4 v-if="selectedNode.props && (selectedNode.props.name || selectedNode.props.title)">
              {{ selectedNode.props.name || selectedNode.props.title }}
            </h4>
            <table class="props">
              <tbody>
                <tr v-for="(v, k) in flatProps(selectedNode.props)" :key="k">
                  <th>{{ k }}</th>
                  <td><pre>{{ v }}</pre></td>
                </tr>
              </tbody>
            </table>
            <h5 v-if="selectedEdges.length">Edges ({{ selectedEdges.length }})</h5>
            <ul class="edge-list">
              <li v-for="(e, i) in selectedEdges" :key="i">
                <code>{{ e.dir }}</code>
                <span class="etype">{{ e.type }}</span>
                <code>{{ e.otherLabel }}:{{ e.otherKey }}</code>
                <span v-if="e.props && Object.keys(e.props).length" class="muted small">
                  {{ JSON.stringify(e.props) }}
                </span>
              </li>
            </ul>
          </div>
        </aside>
      </div>
      <p v-if="graphError" class="error">{{ graphError }}</p>
    </section>

    <section v-if="reportMarkdown" class="card">
      <h2>5. Report</h2>
      <pre class="report">{{ reportMarkdown }}</pre>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import * as d3 from 'd3'
import {
  deleteStudy,
  getGraph,
  getRun,
  getRunLog,
  getRunReportMarkdown,
  listStudies,
  registerStudyFromDisk,
  startRun,
  uploadStudy
} from '../api/v2'

const studies = ref([])
const selectedStudyId = ref('')
const newStudyPath = ref('seeds/bbc_panel/study.json')
const pickedFile = ref(null)

const registering = ref(false)
const registerError = ref('')
const uploading = ref(false)
const uploadError = ref('')
const uploadOk = ref(null)
const deletingId = ref('')

const running = ref(false)
const runError = ref('')

const activeRun = ref(null)
const reportMarkdown = ref('')
const graphData = ref(null)
const graphError = ref('')
const graphSvg = ref(null)
const graphWidth = 620
const graphHeight = 520
const selectedNode = ref(null)
let pollHandle = null
let simulation = null

const logText = computed(() => (activeRun.value?.log || []).join('\n'))
const selectedStudy = computed(() =>
  studies.value.find(s => s.study_id === selectedStudyId.value) || null
)

const selectedEdges = computed(() => {
  if (!selectedNode.value || !graphData.value) return []
  const id = selectedNode.value.id
  const byId = new Map(graphData.value.nodes.map(n => [n.id, n]))
  const out = []
  for (const e of graphData.value.edges) {
    if (e.source === id) {
      const other = byId.get(e.target)
      out.push({ dir: '→', type: e.type, otherLabel: other?.label, otherKey: other?.key, props: e.props })
    } else if (e.target === id) {
      const other = byId.get(e.source)
      out.push({ dir: '←', type: e.type, otherLabel: other?.label, otherKey: other?.key, props: e.props })
    }
  }
  return out
})

function aiStr(v) {
  if (v === null || v === undefined) return '—'
  return Number(v).toFixed(1)
}

function humanSize(n) {
  if (!n && n !== 0) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

function flatProps(p) {
  if (!p) return {}
  const out = {}
  for (const [k, v] of Object.entries(p)) {
    if (k.startsWith('_') && k !== '_key') continue
    out[k] = typeof v === 'object' && v !== null ? JSON.stringify(v, null, 2) : v
  }
  return out
}

async function onDeleteStudy(s) {
  if (!s || !s.study_id) return
  if (!window.confirm(`Delete study "${s.study_id}"? Run history is kept; only the registered study and its uploaded files are removed.`)) return
  deletingId.value = s.study_id
  try {
    await deleteStudy(s.study_id)
    if (selectedStudyId.value === s.study_id) selectedStudyId.value = ''
    await refreshStudies()
  } catch (err) {
    window.alert('Delete failed: ' + String(err?.response?.data?.error || err?.message || err))
  } finally {
    deletingId.value = ''
  }
}

async function refreshStudies() {
  try {
    const res = await listStudies()
    studies.value = res.data || []
    if (!selectedStudyId.value && studies.value.length) {
      selectedStudyId.value = studies.value[0].study_id
    }
  } catch (err) {
    console.error('listStudies failed', err)
  }
}

function onFilePicked(ev) {
  uploadError.value = ''
  pickedFile.value = ev.target.files && ev.target.files[0] ? ev.target.files[0] : null
}

async function uploadPicked() {
  if (!pickedFile.value) return
  uploadError.value = ''
  uploadOk.value = null
  uploading.value = true
  try {
    const res = await uploadStudy(pickedFile.value)
    const rec = res && res.data
    if (rec && rec.study_id) {
      selectedStudyId.value = rec.study_id
      uploadOk.value = {
        study_id:      rec.study_id,
        registered_at: rec.registered_at,
        suffixed:      /__\d+$/.test(rec.study_id),
      }
    }
    pickedFile.value = null
    await refreshStudies()
  } catch (err) {
    uploadError.value = String(err?.response?.data?.error || err?.message || err)
  } finally {
    uploading.value = false
  }
}

async function registerStudy() {
  registerError.value = ''
  registering.value = true
  try {
    const res = await registerStudyFromDisk(newStudyPath.value.trim())
    if (res && res.data && res.data.study_id) {
      selectedStudyId.value = res.data.study_id
    }
    await refreshStudies()
  } catch (err) {
    registerError.value = String(err?.response?.data?.error || err?.message || err)
  } finally {
    registering.value = false
  }
}

const rounds = ref(2)
const skipNeo4j = ref(false)
const noLlmNarrator = ref(false)

async function kickOffRun() {
  runError.value = ''
  running.value = true
  reportMarkdown.value = ''
  selectedNode.value = null
  graphData.value = null
  try {
    const res = await startRun({
      study_id: selectedStudyId.value,
      rounds: rounds.value,
      skip_neo4j: skipNeo4j.value,
      no_llm_narrator: noLlmNarrator.value
    })
    activeRun.value = res.data
    pollUntilDone()
  } catch (err) {
    runError.value = String(err?.message || err)
    running.value = false
  }
}

async function pollUntilDone() {
  if (!activeRun.value) return
  const id = activeRun.value.run_id
  pollHandle = setInterval(async () => {
    try {
      const [statusRes, logRes] = await Promise.all([getRun(id), getRunLog(id)])
      activeRun.value = { ...statusRes.data, log: logRes.data || [] }
      if (activeRun.value.status === 'done') {
        clearInterval(pollHandle)
        pollHandle = null
        running.value = false
        const md = await getRunReportMarkdown(id)
        reportMarkdown.value = md
        await loadAndRenderGraph(activeRun.value.study_id)
      } else if (activeRun.value.status === 'failed') {
        clearInterval(pollHandle)
        pollHandle = null
        running.value = false
        runError.value = activeRun.value.error || 'run failed'
      }
    } catch (err) {
      console.error('poll error', err)
    }
  }, 2000)
}

async function loadAndRenderGraph(study_id) {
  graphError.value = ''
  graphData.value = null
  selectedNode.value = null
  if (skipNeo4j.value) return
  try {
    const graph_id = `v2_${study_id}`
    const res = await getGraph(graph_id)
    graphData.value = res.data
    await nextTick()
    renderGraph(res.data)
  } catch (err) {
    graphError.value =
      'Could not load graph from Neo4j. ' +
      'On Railway, this happens when the graphdb service is offline. ' +
      'The run, metrics, and report above are unaffected.'
  }
}

function renderGraph(data) {
  if (simulation) simulation.stop()
  const svg = d3.select(graphSvg.value)
  svg.selectAll('*').remove()

  const width = graphWidth
  const height = graphHeight

  const links = data.edges.map(e => ({
    source: e.source,
    target: e.target,
    type: e.type,
    propensity: e.props && e.props.propensity ? e.props.propensity : 0.5
  }))
  const nodes = data.nodes.map(n => ({
    id: n.id,
    label: n.label,
    key: n.key,
    raw: n,
    name: (n.props && (n.props.name || n.props.title)) || n.key
  }))

  const labelColor = {
    Panelist: '#2563eb',
    Genre:    '#65a30d',
    Slot:     '#b45309',
    Brief:    '#be185d'
  }
  const labelRadius = {
    Panelist: 12,
    Genre:    9,
    Slot:     9,
    Brief:    14
  }

  simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id)
      .distance(d => 60 + (1 - (d.propensity || 0.5)) * 80)
      .strength(d => 0.2 + (d.propensity || 0.5) * 0.6))
    .force('charge', d3.forceManyBody().strength(-220))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collide', d3.forceCollide().radius(d => (labelRadius[d.label] || 10) + 4))

  const link = svg.append('g')
    .attr('stroke', '#cbd5e1')
    .selectAll('line')
    .data(links)
    .enter().append('line')
    .attr('stroke-width', d => 0.5 + (d.propensity || 0.5) * 2.5)
    .attr('stroke-opacity', d => 0.25 + (d.propensity || 0.5) * 0.55)

  const node = svg.append('g')
    .selectAll('g')
    .data(nodes)
    .enter().append('g')
    .style('cursor', 'pointer')
    .on('click', (event, d) => {
      selectedNode.value = d.raw
      svg.selectAll('g.node-group circle').attr('stroke-width', 1.5)
      d3.select(event.currentTarget).select('circle').attr('stroke-width', 3.5)
    })
    .attr('class', 'node-group')
    .call(d3.drag()
      .on('start', (event, d) => {
        if (!event.active) simulation.alphaTarget(0.3).restart()
        d.fx = d.x; d.fy = d.y
      })
      .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y })
      .on('end', (event, d) => {
        if (!event.active) simulation.alphaTarget(0)
        d.fx = null; d.fy = null
      }))

  node.append('circle')
    .attr('r', d => labelRadius[d.label] || 8)
    .attr('fill', d => labelColor[d.label] || '#6b7280')
    .attr('stroke', '#fff')
    .attr('stroke-width', 1.5)

  node.append('title')
    .text(d => `${d.label}: ${d.name}`)

  node.append('text')
    .text(d => d.label === 'Panelist' ? d.name.split(' ')[0] : d.key)
    .attr('x', d => (labelRadius[d.label] || 8) + 4)
    .attr('y', 4)
    .attr('font-size', 11)
    .attr('fill', '#1f2937')

  simulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y)
    node.attr('transform', d => `translate(${d.x},${d.y})`)
  })
}

onMounted(refreshStudies)
onUnmounted(() => {
  if (pollHandle) clearInterval(pollHandle)
  if (simulation) simulation.stop()
})
</script>

<style scoped>
.v2-shell {
  max-width: 1100px;
  margin: 24px auto;
  padding: 0 16px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  color: #1f2937;
}
.v2-header h1 { margin: 0 0 6px 0; font-size: 24px; }
.muted { color: #6b7280; font-size: 14px; }
.muted.small { font-size: 12px; }
.error { color: #b91c1c; font-size: 14px; }
.ok { color: #166534; font-size: 13px; }
.ok.small { font-size: 12px; }

.card {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 16px 20px;
  margin: 16px 0;
}
.card h2 { margin: 0 0 12px 0; font-size: 17px; }

.row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.row .grow { flex: 1; }
.row input[type="text"], .row input[type="number"] {
  border: 1px solid #d1d5db; border-radius: 6px; padding: 6px 10px; font-size: 14px;
}
.row .narrow { width: 60px; }
.row label { font-size: 14px; }
.checkbox { display: inline-flex; align-items: center; gap: 6px; }

.file-pick {
  display: inline-flex; align-items: center; gap: 8px;
  border: 1px dashed #94a3b8; border-radius: 6px; padding: 6px 10px;
  cursor: pointer; font-size: 13px;
}
.file-pick input[type="file"] { font-size: 12px; }

.from-disk { margin-top: 4px; margin-bottom: 8px; }
.from-disk summary { font-size: 13px; color: #6b7280; cursor: pointer; }
.from-disk .row { margin-top: 8px; }

button {
  border: 1px solid #2563eb; background: #2563eb; color: #fff;
  border-radius: 6px; padding: 6px 14px; font-size: 14px; cursor: pointer;
}
button:disabled { opacity: 0.5; cursor: not-allowed; }
button.ghost {
  background: transparent; color: #1f2937; border-color: #cbd5e1;
}
button.ghost.danger {
  color: #b91c1c; border-color: #fecaca; padding: 4px 10px; font-size: 12px;
}
button.ghost.danger:hover:not(:disabled) {
  background: #fef2f2; border-color: #fca5a5;
}

table.grid { width: 100%; border-collapse: collapse; font-size: 13px; }
table.grid th, table.grid td {
  text-align: left; padding: 6px 10px; border-bottom: 1px solid #f3f4f6;
  vertical-align: middle;
}
table.grid tbody tr { cursor: pointer; }
table.grid tr.selected { background: #eef2ff; }
table.grid td.file-cell { display: flex; gap: 10px; }
.link { color: #2563eb; font-size: 13px; text-decoration: none; }
.link:hover { text-decoration: underline; }
.downloads { margin-left: 12px; font-size: 13px; display: inline-flex; gap: 12px; }

.selection {
  margin: 0 0 10px 0; padding: 8px 12px; border-radius: 6px;
  background: #f0f9ff; border: 1px solid #bae6fd; font-size: 14px;
}

pre.log {
  background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 8px;
  font-size: 12.5px; line-height: 1.4; overflow-x: auto; white-space: pre-wrap;
  max-height: 480px; overflow-y: auto;
}
pre.report {
  background: #f8fafc; color: #1f2937;
  padding: 14px; border-radius: 8px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,
               "Liberation Mono", "Courier New", monospace;
  font-size: 12.5px; line-height: 1.55;
  white-space: pre;
  overflow: auto;
  max-height: 720px;
  border: 1px solid #e2e8f0;
}

.headline { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 12px;
  background: #f0fdf4; padding: 10px 14px; border-radius: 8px; font-size: 14px; }

.graph-flex {
  display: flex; gap: 14px; align-items: stretch; flex-wrap: wrap;
}
.graph-svg {
  display: block;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  flex: 1 1 560px;
  height: 520px;
}
.graph-svg text { user-select: none; pointer-events: none; }

.node-panel {
  flex: 1 1 320px;
  max-width: 420px;
  border: 1px solid #e5e7eb; border-radius: 8px;
  padding: 12px; background: #ffffff;
  overflow: auto; max-height: 520px;
  font-size: 13px;
}
.node-panel h4 { margin: 6px 0 8px; font-size: 15px; }
.node-panel h5 { margin: 12px 0 6px; font-size: 13px; color: #374151; }
.node-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

table.props { width: 100%; border-collapse: collapse; font-size: 12.5px; }
table.props th {
  text-align: left; vertical-align: top; padding: 4px 8px 4px 0;
  color: #6b7280; font-weight: 500; width: 35%;
}
table.props td { padding: 4px 0; }
table.props pre {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  white-space: pre-wrap; word-break: break-word;
}

.edge-list { padding-left: 0; list-style: none; margin: 4px 0 0; }
.edge-list li {
  padding: 3px 0; border-bottom: 1px dashed #f1f5f9;
  display: flex; gap: 6px; flex-wrap: wrap; align-items: center;
}
.edge-list .etype { font-size: 11px; color: #64748b; }

.graph-legend { display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0 12px; }
.legend-pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 2px 10px; border-radius: 9999px; font-size: 12px;
  background: #f3f4f6; color: #1f2937;
}
.legend-pill::before {
  content: ""; width: 9px; height: 9px; border-radius: 50%;
  display: inline-block; background: #6b7280;
}
.legend-pill[data-lbl="Panelist"]::before { background: #2563eb; }
.legend-pill[data-lbl="Genre"]::before    { background: #65a30d; }
.legend-pill[data-lbl="Slot"]::before     { background: #b45309; }
.legend-pill[data-lbl="Brief"]::before    { background: #be185d; }
</style>
