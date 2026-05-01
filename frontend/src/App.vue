<template>
  <router-view />
  <div class="build-badge" :title="badgeTitle" :class="{ mismatch: shaMismatch }">
    ui {{ buildSha }} · {{ buildDateShort }}
    <span v-if="serverSha"> · srv {{ serverSha }}</span>
  </div>
</template>

<script setup>
// 使用 Vue Router 来管理页面
import { computed, onMounted, ref } from 'vue'
import { getVersion } from './api/v2'

// Injected at compile time by vite.config.js (define).
// eslint-disable-next-line no-undef
const buildTime = typeof __APP_BUILD_TIME__ !== 'undefined' ? __APP_BUILD_TIME__ : 'dev'
// eslint-disable-next-line no-undef
const buildSha  = typeof __APP_BUILD_SHA__  !== 'undefined' ? __APP_BUILD_SHA__  : 'dev'

const buildDateShort = computed(() => {
  const t = new Date(buildTime)
  if (isNaN(t)) return buildTime
  const pad = n => String(n).padStart(2, '0')
  return `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())} ` +
         `${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}Z`
})

const serverSha = ref('')
const serverStartedAt = ref('')

onMounted(async () => {
  try {
    const r = await getVersion()
    const d = r?.data?.data || r?.data || {}
    serverSha.value = d.git_sha || ''
    serverStartedAt.value = d.started_at || ''
  } catch (_) { /* ignore — backend may not be reachable */ }
})

const shaMismatch = computed(
  () => serverSha.value && buildSha !== 'dev' && serverSha.value !== buildSha
)

const badgeTitle = computed(() => {
  const ui = `UI built ${buildTime} (${buildSha})`
  const srv = serverSha.value
    ? `Server ${serverSha.value} started ${serverStartedAt.value}`
    : 'Server version unknown'
  return `${ui}\n${srv}`
})
</script>

<style>
/* 全局样式重置 */
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

#app {
  font-family: 'JetBrains Mono', 'Space Grotesk', 'Noto Sans SC', monospace;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  color: #000000;
  background-color: #ffffff;
}

/* 滚动条样式 */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: #f1f1f1;
}

::-webkit-scrollbar-thumb {
  background: #000000;
}

::-webkit-scrollbar-thumb:hover {
  background: #333333;
}

/* 全局按钮样式 */
button {
  font-family: inherit;
}

.build-badge {
  position: fixed;
  bottom: 10px;
  right: 12px;
  z-index: 99999;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  font-weight: 500;
  color: #ffffff;
  background: #0f172a;
  border: 1px solid #1e293b;
  border-radius: 6px;
  padding: 6px 10px;
  pointer-events: auto;
  user-select: text;
  white-space: nowrap;
  box-shadow: 0 4px 10px rgba(15, 23, 42, 0.18);
}
.build-badge.mismatch {
  color: #1e293b;
  background: #fbbf24;
  border-color: #d97706;
}
</style>
