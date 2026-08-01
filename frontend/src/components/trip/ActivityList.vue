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
      <div class="activity-visual" :class="{ photo: activity.image_url }">
        <img v-if="activity.image_url" :src="activity.image_url" :alt="activity.place.name" loading="lazy" />
        <template v-else>{{ visuals[activity.type] || '📍' }}</template>
      </div>
      <div class="activity-copy">
        <strong><b>{{ index + 1 }}</b>{{ activity.place.name }}</strong>
        <span>
          {{ activity.type === 'hotel' ? '住宿' : `停留 ${Math.round(activity.duration_minutes / 60 * 10) / 10}h` }}
          · {{ activity.place.city }}
        </span>
        <span v-if="activity.planned_start && activity.planned_end" class="activity-meta">
          {{ activity.planned_start.slice(11, 16) }}–{{ activity.planned_end.slice(11, 16) }}
          <template v-if="activity.opening_hours"> · {{ activity.opening_hours.text }}</template>
        </span>
        <span v-if="activity.ticket_or_price" class="activity-meta">
          {{ activity.ticket_or_price.estimated ? '预计' : '' }}
          ¥{{ activity.ticket_or_price.minimum }}–{{ activity.ticket_or_price.maximum }}
        </span>
        <span v-if="activity.source_records?.length" class="activity-source">
          来源：{{ [...new Set(activity.source_records.map((item) => item.provider))].join('、') }}
        </span>
        <p v-if="activity.user_note" class="activity-note">{{ activity.user_note }}</p>
        <a
          v-if="activity.detail_url || activity.source_records?.find((item) => item.url)"
          class="activity-detail-link"
          :href="activity.detail_url || activity.source_records?.find((item) => item.url)?.url"
          target="_blank"
          rel="noreferrer"
          @click.stop
        >查看景点 / 商户详情与来源</a>
        <div>
          <button @click.stop="$emit('remove', activity.id)"><Trash2 />移除</button>
          <button @click.stop="$emit('select', activity)"><MessageCircle />问 AI</button>
        </div>
      </div>
    </article>
  </div>
  <div v-else class="empty-state"><MapPin />当前分类暂无已选节点</div>
</template>
