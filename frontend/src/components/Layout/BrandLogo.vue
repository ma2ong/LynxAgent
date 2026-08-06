<template>
  <!-- 与 public/favicon.svg 同一图形，改一处两处都要改。
       内联而非 <img src="/favicon.svg">：侧边栏 26px、移动端顶栏 22px，内联能跟着字号
       缩放且不额外发一次请求。 -->
  <svg :width="size" :height="size" viewBox="0 0 64 64" role="img" aria-label="AStockPick">
    <rect width="64" height="64" rx="15" fill="#0f172a" />
    <path d="M32 12 L52 52" stroke="#fff" stroke-width="7" stroke-linecap="round" />
    <path d="M32 12 L12 52" stroke="#fff" stroke-width="7" stroke-linecap="round" />
    <path d="M21.5 41 H42.5" stroke="#fff" stroke-width="6.5" stroke-linecap="round" />
    <path d="M32 21 L41 37.5 H23 Z" fill="#ef4444" />
  </svg>
</template>

<script setup lang="ts">
defineProps<{ size?: number | string }>()
</script>
