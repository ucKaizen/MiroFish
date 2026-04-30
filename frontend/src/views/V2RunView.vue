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

    <section v-if="reportMarkdown" class="card">
      <h2>4. Report</h2>
      <pre class="report">{{ reportMarkdown }}</pre>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
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
let pollHandle = null

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

onMounted(refreshStudies)
onUnmounted(() => {
  if (pollHandle) clearInterval(pollHandle)
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
</style>
