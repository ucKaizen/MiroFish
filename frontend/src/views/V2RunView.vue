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
        <input
          v-model="newStudyPath"
          type="text"
          placeholder="seeds/bbc_panel/study.json"
          class="grow"
        />
        <button :disabled="!newStudyPath || registering" @click="registerStudy">
          {{ registering ? 'Registering…' : 'Register study' }}
        </button>
      </div>
      <p v-if="registerError" class="error">{{ registerError }}</p>

      <table v-if="studies.length" class="grid">
        <thead>
          <tr>
            <th>study_id</th>
            <th>name</th>
            <th>panelists</th>
            <th>edges</th>
            <th>brief</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in studies" :key="s.study_id"
              :class="{ selected: selectedStudyId === s.study_id }">
            <td><code>{{ s.study_id }}</code></td>
            <td>{{ s.name }}</td>
            <td>{{ s.panelists }}</td>
            <td>{{ s.edges }}</td>
            <td>{{ s.brief.title }} ({{ s.brief.air_date }})</td>
            <td>
              <button @click="selectedStudyId = s.study_id">Select</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">No studies registered yet.</p>
    </section>

    <section class="card">
      <h2>2. Run</h2>
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
      <p v-if="!selectedStudyId" class="muted">Select a study above first.</p>
      <p v-if="runError" class="error">{{ runError }}</p>
    </section>

    <section v-if="activeRun" class="card">
      <h2>3. Run status — <code>{{ activeRun.run_id }}</code></h2>
      <p>
        <strong>{{ activeRun.status }}</strong>
        — step {{ activeRun.step }} / {{ activeRun.step_total }}
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
        {{ graphData.node_count }} nodes, {{ graphData.edge_count }} edges. Hover a
        node for details. Drag to reposition.
      </p>
      <div class="graph-legend">
        <span class="legend-pill" data-lbl="Panelist">Panelist</span>
        <span class="legend-pill" data-lbl="Genre">Genre</span>
        <span class="legend-pill" data-lbl="Slot">Slot</span>
        <span class="legend-pill" data-lbl="Brief">Brief</span>
      </div>
      <svg ref="graphSvg" class="graph-svg" :width="graphWidth" :height="graphHeight"></svg>
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
  getGraph,
  getRun,
  getRunLog,
  getRunReportMarkdown,
  listStudies,
  registerStudyFromDisk,
  startRun
} from '../api/v2'

const studies = ref([])
const selectedStudyId = ref('')
const newStudyPath = ref('seeds/bbc_panel/study.json')

const registering = ref(false)
const registerError = ref('')

const running = ref(false)
const runError = ref('')

const activeRun = ref(null)
const reportMarkdown = ref('')
const graphData = ref(null)
const graphError = ref('')
const graphSvg = ref(null)
const graphWidth = 880
const graphHeight = 520
let pollHandle = null
let simulation = null

const logText = computed(() => (activeRun.value?.log || []).join('\n'))

function aiStr(v) {
  if (v === null || v === undefined) return '—'
  return Number(v).toFixed(1)
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

async function registerStudy() {
  registerError.value = ''
  registering.value = true
  try {
    await registerStudyFromDisk(newStudyPath.value.trim())
    await refreshStudies()
  } catch (err) {
    registerError.value = String(err?.message || err)
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
  if (skipNeo4j.value) {
    // Run was started with skip-Neo4j; nothing was written.
    return
  }
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

  // Pre-scale propensity edges so that strong propensities pull harder.
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
    name: (n.props && (n.props.name || n.props.title)) || n.key,
    age: n.props && n.props.age,
    region: n.props && n.props.region,
    occupation: n.props && n.props.occupation
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
    .text(d => {
      if (d.label === 'Panelist') {
        return `${d.name}\n${d.age || ''} · ${d.region || ''}\n${d.occupation || ''}`
      }
      return `${d.label}: ${d.key}`
    })

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
  max-width: 980px;
  margin: 24px auto;
  padding: 0 16px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  color: #1f2937;
}
.v2-header h1 { margin: 0 0 6px 0; font-size: 24px; }
.muted { color: #6b7280; font-size: 14px; }
.error { color: #b91c1c; font-size: 14px; }

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

button {
  border: 1px solid #2563eb; background: #2563eb; color: #fff;
  border-radius: 6px; padding: 6px 14px; font-size: 14px; cursor: pointer;
}
button:disabled { opacity: 0.5; cursor: not-allowed; }

table.grid { width: 100%; border-collapse: collapse; font-size: 13px; }
table.grid th, table.grid td {
  text-align: left; padding: 6px 10px; border-bottom: 1px solid #f3f4f6;
}
table.grid tr.selected { background: #eef2ff; }

pre.log, pre.report {
  background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 8px;
  font-size: 12.5px; line-height: 1.4; overflow-x: auto; white-space: pre-wrap;
  max-height: 480px; overflow-y: auto;
}
pre.report { background: #f8fafc; color: #1f2937; }

.headline { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 12px;
  background: #f0fdf4; padding: 10px 14px; border-radius: 8px; font-size: 14px; }

.graph-svg {
  display: block;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  width: 100%;
  height: 520px;
}
.graph-svg text { user-select: none; pointer-events: none; }
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
