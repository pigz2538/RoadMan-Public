<script setup lang="ts">
import { MapPin, MessageCircle, Trash2 } from '@lucide/vue'
import type { Activity } from '../../types/trip'
import { humanizeDisplayText, humanizeProvider } from '../../utils/displayLabels'

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

function formatStayDuration(minutes: number) {
  const rounded = Math.max(0, Math.round(minutes))
  const hours = Math.floor(rounded / 60)
  const rest = rounded % 60
  if (hours && rest) return `${hours}小时${rest}分钟`
  if (hours) return `${hours}小时`
  return `${rest}分钟`
}

function activityKindLabel(activity: Activity) {
  if (activity.type === 'meal' && activity.in_transit) return '途中用餐'
  const meal = activity.user_note?.match(/每日(早餐|午餐|晚餐)安排/)
  if (meal) return meal[1]
  if (activity.type === 'hotel') return '住宿'
  return `停留 ${formatStayDuration(activity.duration_minutes)}`
}

const reservationLabels: Record<string, string> = {
  required: '需预约',
  recommended: '建议预约',
  not_required: '无需预约',
  unknown: '预约待核查',
}
</script>

<template>
  <div v-if="activities.length" class="activity-list">
    <article
      v-for="(activity, index) in activities"
      :key="activity.id"
      :class="{ selected: selectedId === activity.id }"
      :style="{ '--item-index': index }"
      @click="$emit('select', activity)"
    >
      <div class="activity-visual" :class="{ photo: activity.image_url }">
        <img v-if="activity.image_url" :src="activity.image_url" :alt="activity.place.name" loading="lazy" />
        <template v-else>{{ visuals[activity.type] || '📍' }}</template>
      </div>
      <div class="activity-copy">
        <strong><b>{{ index + 1 }}</b>{{ activity.place.name }}</strong>
        <span>
          {{ activityKindLabel(activity) }}
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
        <span v-else-if="activity.type === 'attraction'" class="activity-meta activity-unknown">
          门票信息：{{ activity.ticket_status === 'free' ? '免费' : '暂未返回，出发前复核' }}
        </span>
        <span v-if="activity.ticket_name" class="activity-meta">票种：{{ activity.ticket_name }}</span>
        <span v-if="activity.parking_note" class="activity-meta">停车：{{ activity.parking_note }}</span>
        <span v-if="activity.parking_or_price" class="activity-meta">
          停车费：{{ activity.parking_or_price.estimated ? '约' : '' }}¥{{ activity.parking_or_price.minimum }}<template v-if="activity.parking_or_price.maximum !== activity.parking_or_price.minimum">-{{ activity.parking_or_price.maximum }}</template>
        </span>
        <span v-if="activity.information_status" class="activity-meta">
          信息完整度：{{ activity.information_status === 'complete' ? '多源已核对' : activity.information_status === 'partial' ? '部分来源' : '暂不可用' }}
          <template v-if="activity.information_sources_count">（{{ activity.information_sources_count }} 个来源）</template>
        </span>
        <div v-if="activity.reservation_status || activity.risk_tags?.length" class="activity-checks">
          <span
            v-if="activity.reservation_status"
            :class="['activity-check', `reservation-${activity.reservation_status}`]"
            :title="activity.reservation_note"
          >{{ reservationLabels[activity.reservation_status] || '预约待核查' }}</span>
          <span
            v-for="tag in (activity.risk_tags || []).slice(0, 3)"
            :key="tag"
            :class="['activity-check', `risk-${activity.risk_level || 'moderate'}`]"
            :title="activity.risk_note"
          >{{ tag }}</span>
        </div>
        <p v-if="activity.reservation_note" class="activity-check-note">{{ humanizeDisplayText(activity.reservation_note) }}</p>
        <p v-if="activity.risk_note" class="activity-check-note risk-note">{{ humanizeDisplayText(activity.risk_note) }}</p>
        <span v-if="activity.source_records?.length" class="activity-source">
          来源：{{ [...new Set(activity.source_records.map((item) => humanizeProvider(item.provider)))].join('、') }}
        </span>
        <p v-if="activity.user_note" class="activity-note">{{ humanizeDisplayText(activity.user_note) }}</p>
        <p v-if="activity.description" class="activity-description">{{ humanizeDisplayText(activity.description) }}</p>
        <a
          v-if="activity.detail_url || activity.official_url || activity.booking_url || activity.source_records?.find((item) => item.url)"
          class="activity-detail-link"
          :href="activity.detail_url || activity.official_url || activity.booking_url || activity.source_records?.find((item) => item.url)?.url"
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
