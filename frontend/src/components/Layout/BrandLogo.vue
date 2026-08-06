<template>
  <!-- 与 public/favicon.svg 同一图形，改一处两处都要改。
       做成组件而不是 <img src="/favicon.svg">：侧边栏和移动端顶栏尺寸不同，内联 SVG
       能跟着字号缩放且不额外发一次请求；旧实现是一个 📈 emoji，在不同系统里长相不一致。 -->
  <svg :width="size" :height="size" viewBox="0 0 64 64" role="img" aria-label="AStockPick">
    <rect width="64" height="64" rx="14" fill="#0f172a" />
    <g fill="#64748b">
      <rect x="13" y="34" width="7" height="14" rx="3.5" />
      <rect x="24" y="28" width="7" height="20" rx="3.5" />
      <rect x="46" y="31" width="7" height="17" rx="3.5" />
    </g>
    <rect x="35" y="19" width="7" height="29" rx="3.5" fill="#ef4444" />
    <circle cx="38.5" cy="11" r="3.5" fill="#ef4444" />
  </svg>
</template>

<script setup lang="ts">
defineProps<{ size?: number | string }>()
</script>
