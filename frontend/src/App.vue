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
  bottom: 6px;
  right: 8px;
  z-index: 9999;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10.5px;
  color: #64748b;
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  padding: 2px 6px;
  pointer-events: auto;
  user-select: text;
  white-space: nowrap;
}
.build-badge.mismatch {
  color: #92400e;
  background: #fef3c7;
  border-color: #fbbf24;
}
</style>
