<script setup lang="ts">
import { computed } from 'vue'
import type { DayPlan } from '../../types/trip'

const props = defineProps<{ day?: DayPlan; activeStageId?: string }>()
const activeIndex = computed(() => props.day?.stages.findIndex((item) => item.id === props.activeStageId) ?? 0)
</script>

<template>
  <div class="mock-map" aria-label="武汉至庐山示例路线地图">
    <svg viewBox="0 0 800 600" role="img">
      <defs>
        <linearGradient id="land" x1="0" y1="0" x2="1" y2="1">
          <stop stop-color="#f7fbf5"/><stop offset="1" stop-color="#e6f3ec"/>
        </linearGradient>
        <filter id="routeGlow"><feGaussianBlur stdDeviation="7" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
      </defs>
      <rect width="800" height="600" fill="url(#land)"/>
      <g class="terrain" fill="none" stroke="#dce9df" stroke-width="28" opacity=".85">
        <path d="M-60 120C140 50 200 180 360 100S650 80 850 30"/>
        <path d="M-50 410C130 330 240 450 410 350S650 290 850 360"/>
      </g>
      <g class="roads" fill="none" stroke="#cad4e1" stroke-width="6">
        <path d="M-20 500C120 460 180 380 310 400S520 330 820 210"/>
        <path d="M50 30C130 160 300 210 450 170S650 120 780 30"/>
        <path d="M170 620C230 480 210 330 350 210S600 190 650 -20"/>
        <path d="M-20 260C170 280 290 240 430 290S700 350 830 310"/>
      </g>
      <g class="labels">
        <text x="95" y="500">武汉</text><text x="315" y="360">黄石</text>
        <text x="510" y="265">九江</text><text x="650" y="150">庐山</text>
        <text x="185" y="185">长江</text>
      </g>
      <path class="route-shadow" d="M110 490C175 435 230 420 302 386S410 325 478 298S570 240 628 198C663 170 648 136 690 112"/>
      <path
        class="route-line"
        :class="{ muted: activeIndex > 0 }"
        d="M110 490C175 435 230 420 302 386S410 325 478 298"
      />
      <path
        class="route-line"
        :class="{ muted: activeIndex === 0 }"
        d="M478 298C535 276 570 240 628 198C663 170 648 136 690 112"
      />
      <g v-for="(point, index) in [[110,490],[478,298],[690,112]]" :key="index" class="map-pin" :transform="`translate(${point[0]} ${point[1]})`">
        <circle r="19"/><text y="6">{{ index + 1 }}</text>
      </g>
      <g class="risk-pin" transform="translate(628 198)">
        <path d="M0-18L17 13h-34z"/><text y="8">!</text>
      </g>
    </svg>
    <div class="map-controls"><button>＋</button><button>－</button><button>◇</button></div>
    <div class="map-legend">
      <span><i class="active-route" />当前阶段</span>
      <span><i class="day-route" />当日其他阶段</span>
      <span><i class="risk-route" />风险</span>
    </div>
  </div>
</template>
