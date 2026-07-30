<script setup lang="ts">
import { computed, ref } from 'vue'
import { Bot, Send } from '@lucide/vue'
import { useTripStore } from '../../stores/trip'

const store = useTripStore()
const message = ref('')
const messages = ref([
  { side: 'ai', text: '检测到当前阶段包含盘山路段，建议在天黑前抵达牯岭镇。需要我给出风险较低的时间调整吗？' },
  { side: 'user', text: '好的，给我看一下修改建议' },
])

const contextText = computed(() => {
  if (store.selectedNodeId) return `当前已选：${store.selectedNodeId}`
  return '请先在地图或左栏选择节点'
})

function send() {
  const text = message.value.trim()
  if (!text) return
  messages.value.push({ side: 'user', text })
  message.value = ''
  if (store.selectedNodeId) store.patchVisible = true
  messages.value.push({
    side: 'ai',
    text: store.selectedNodeId
      ? '我已生成修改预览。正式计划尚未变化，请先核对影响范围。'
      : '请先选择要修改的活动或阶段，我不会在目标不明确时改动正式行程。',
  })
}
</script>

<template>
  <aside class="agent-panel glass-card">
    <header><Bot /><strong>Agent 行程助理</strong><span class="online-dot" /></header>
    <div class="context-chip">{{ contextText }}</div>
    <div class="chat-stream">
      <div v-for="(item, index) in messages" :key="index" :class="['message', item.side]">{{ item.text }}</div>
      <div v-if="store.patchVisible" class="patch-card">
        <span>PLAN PATCH · 修改建议</span>
        <strong>提前 30 分钟离开黄石服务区</strong>
        <dl>
          <div><dt>时间变化</dt><dd>-30 min</dd></div>
          <div><dt>影响范围</dt><dd>阶段 2、如琴湖</dd></div>
          <div><dt>风险变化</dt><dd class="good">夜间山路 ↓</dd></div>
        </dl>
        <div><button @click="store.patchVisible = false">取消</button><button class="apply" @click="store.patchVisible = false">应用修改</button></div>
      </div>
    </div>
    <div class="suggestions">
      <button @click="message = '推荐附近餐厅'">推荐附近餐厅</button>
      <button @click="message = '当前路况如何？'">当前路况如何？</button>
      <button @click="message = '阶段风险提示'">阶段风险提示</button>
    </div>
    <form class="chat-input" @submit.prevent="send">
      <input v-model="message" placeholder="输入需求…" />
      <button aria-label="发送"><Send /></button>
    </form>
  </aside>
</template>
