<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Activity, ArrowLeft, Database, Gauge, RadioTower } from '@lucide/vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const metrics = ref<Record<string, any> | null>(null)
const skills = ref<Record<string, any> | null>(null)
const error = ref('')
let timer: number | undefined

async function refresh() {
  try {
    const [metricsResponse, skillsResponse] = await Promise.all([
      fetch('/api/v1/ops/metrics'),
      fetch('/api/v1/skills/health'),
    ])
    if (!metricsResponse.ok || !skillsResponse.ok) throw new Error('监控接口暂不可用')
    metrics.value = await metricsResponse.json()
    skills.value = await skillsResponse.json()
    error.value = ''
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '监控数据加载失败'
  }
}

onMounted(() => {
  void refresh()
  timer = window.setInterval(refresh, 10_000)
})
onUnmounted(() => timer && window.clearInterval(timer))
</script>

<template>
  <main class="ops-page">
    <header>
      <button class="ghost-button" @click="router.push('/home')"><ArrowLeft />返回首页</button>
      <div><span>ROADMAN · OPERATIONS</span><h1>运行监控</h1></div>
      <button class="ghost-button" @click="refresh">立即刷新</button>
    </header>
    <p v-if="error" class="ops-error">{{ error }}</p>
    <section class="ops-grid">
      <article><Gauge /><span>请求总量</span><strong>{{ metrics?.service?.requests ?? 0 }}</strong></article>
      <article><Activity /><span>平均延迟</span><strong>{{ metrics?.service?.average_latency_ms ?? 0 }} ms</strong></article>
      <article><RadioTower /><span>Skill 调用</span><strong>{{ metrics?.skills?.total_calls ?? 0 }}</strong></article>
      <article><Database /><span>缓存命中</span><strong>{{ metrics?.skills?.cache_hits ?? 0 }}</strong></article>
    </section>
    <section class="ops-panel">
      <header><h2>Skill 与外部服务状态</h2><small>每 10 秒自动刷新</small></header>
      <pre>{{ JSON.stringify(skills, null, 2) }}</pre>
    </section>
    <section class="ops-panel">
      <header><h2>请求与调用摘要</h2></header>
      <pre>{{ JSON.stringify(metrics, null, 2) }}</pre>
    </section>
  </main>
</template>
