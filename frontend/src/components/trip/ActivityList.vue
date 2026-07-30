<script setup lang="ts">
import { MapPin, MessageCircle, Trash2 } from '@lucide/vue'
import type { Activity } from '../../types/trip'

defineProps<{ activities: Activity[]; selectedId?: string | null }>()
defineEmits<{
  select: [activity: Activity]
  remove: [id: string]
}>()

const visuals: Record<string, string> = {
  attraction: '🏞️',
  hotel: '🏨',
  meal: '🍜',
  rest: '☕',
  charging: '⚡',
  service: '🛟',
}
</script>

<template>
  <div v-if="activities.length" class="activity-list">
    <article
      v-for="(activity, index) in activities"
      :key="activity.id"
      :class="{ selected: selectedId === activity.id }"
      @click="$emit('select', activity)"
    >
      <div class="activity-visual">{{ visuals[activity.type] || '📍' }}</div>
      <div class="activity-copy">
        <strong><b>{{ index + 1 }}</b>{{ activity.place.name }}</strong>
        <span>停留 {{ Math.round(activity.duration_minutes / 60 * 10) / 10 }}h · {{ activity.place.city }}</span>
        <div>
          <button @click.stop="$emit('remove', activity.id)"><Trash2 />移除</button>
          <button @click.stop="$emit('select', activity)"><MessageCircle />问 AI</button>
        </div>
      </div>
    </article>
  </div>
  <div v-else class="empty-state"><MapPin />当前分类暂无已选节点</div>
</template>
