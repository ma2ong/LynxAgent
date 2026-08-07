<template>
  <!-- 与 public/favicon.svg 同一图形，改一处两处都要改。
       内联而非 <img src="/favicon.svg">：侧边栏 26px、移动端顶栏 22px 两个尺寸，
       内联能跟着字号缩放且不额外发一次请求。
       渐变 id 必须逐实例唯一：本组件在一个页面里会渲染两次（侧边栏 + 移动端顶栏），
       用同一个写死 id 时 url(#id) 只会命中文档顺序里的第一份——桌面端那份在
       display:none 的移动端顶栏里，渐变不参与绘制，徽标底色整块画不出来。 -->
  <svg :width="size" :height="size" viewBox="0 0 64 64" role="img" aria-label="AStockPick">
    <defs>
      <linearGradient :id="gradId" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#f2404a" />
        <stop offset="1" stop-color="#c2101f" />
      </linearGradient>
    </defs>
    <rect width="64" height="64" rx="15" :fill="`url(#${gradId})`" />
    <path
      d="M17 41 L27 31 L35 38 L47 23"
      fill="none"
      stroke="#fff"
      stroke-width="4.6"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
    <circle cx="47" cy="23" r="3.8" fill="#fff" />
  </svg>
</template>

<script setup lang="ts">
import { useId } from 'vue'

defineProps<{ size?: number | string }>()

const gradId = `apGrad-${useId()}`
</script>
