<script setup lang="ts">
import { computed, ref } from 'vue'
import { Bot, Send } from '@lucide/vue'
import { useTripStore } from '../../stores/trip'

const store = useTripStore()
const message = ref('')
const messages = ref([
  { side: 'ai', text: '路线与阶段已经生成。您可以点击地图或下方卡片查看具体路段。' },
])

const contextText = computed(() => {
  if (store.selectedNodeId) return `当前已选：${store.selectedNodeId}`
  return '请先在地图或阶段栏选择目标'
})

function send() {
  const text = message.value.trim()
  if (!text) return
  messages.value.push({ side: 'user', text })
  message.value = ''
  messages.value.push({
    side: 'ai',
    text: '我已记录这条需求。阶段 D 目前支持初次规划与澄清，局部修改将在阶段 G 接入。',
  })
}
</script>

<template>
  <aside class="agent-panel glass-card">
    <header><Bot /><strong>Agent 行程助理</strong><span class="online-dot" /></header>
    <div class="context-chip">{{ contextText }}</div>
    <div class="chat-stream">
      <div
        v-if="store.planningEvent"
        class="message ai"
      >{{ store.planningEvent.label }}（{{ store.planningEvent.progress }}%）</div>
      <div v-for="(item, index) in messages" :key="index" :class="['message', item.side]">
        {{ item.text }}
      </div>
    </div>
    <div class="suggestions">
      <button @click="message = '查看当前阶段详情'">阶段详情</button>
      <button @click="message = '有哪些风险提示？'">风险提示</button>
      <button @click="message = '显示路线来源'">路线来源</button>
    </div>
    <form class="chat-input" @submit.prevent="send">
      <input v-model="message" placeholder="输入需求…" />
      <button aria-label="发送"><Send /></button>
    </form>
  </aside>
</template>
