<template>
  <div>
    <div ref="el" style="width:100%;height:520px;"></div>
    <div v-if="patterns.length" class="pattern-legend" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;">
      <span
        v-for="(p, i) in patterns.slice(0, 8)"
        :key="p.key || i"
        style="display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:2px 8px;border-radius:4px;"
        :style="{ background: bandColor(i, 0.12), border: '1px solid ' + bandColor(i, 0.85) }"
      >
        <i style="width:10px;height:10px;border-radius:2px;display:inline-block;" :style="{ background: bandColor(i, 0.95) }"></i>
        {{ p.name }} {{ Number(p.strength).toFixed(0) }}
      </span>
    </div>
    <el-empty v-if="!hasData" description="暂无K线数据（请先在「数据同步」全量同步）" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps<{ payload: any }>()
const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null
const hasData = computed(() => !!props.payload?.candles?.length)
const patterns = computed(() => props.payload?.patterns || [])

const PALETTE = ['#e6a23c', '#409eff', '#67c23a', '#f56c6c', '#9b59b6', '#1abc9c', '#e84393', '#fd9644']
const bandColor = (i: number, alpha = 1) => {
  const hex = PALETTE[i % PALETTE.length]
  if (alpha >= 1) return hex
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

const buildOption = () => {
  const p = props.payload || {}
  const dates = p.dates || []
  const up = '#ef232a', down = '#14b143'
  const maColors: Record<string, string> = { ma5: '#f6c64b', ma10: '#e6483d', ma20: '#3b82f6', ma30: '#16a34a', ma60: '#8b5cf6' }
  const maSeries = ['ma5', 'ma10', 'ma20', 'ma30', 'ma60'].map((k) => ({
    name: k.toUpperCase(), type: 'line', data: (p.ma?.[k] || []), smooth: true,
    showSymbol: false, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { color: maColors[k], width: 1 }
  }))
  const candles = p.candles || []
  const volColors = candles.map((c: number[]) => (c[1] >= c[0] ? up : down))

  // 形态特征标注：box(框) / circle(空心圆圈) / hline(压力虚线) / arrow(箭头，纵向错开防重叠)
  const ps = (p.patterns || []).slice(0, 6)
  const areaData: any[] = []
  const pointData: any[] = []
  const macdPointData: any[] = []
  const lineData: any[] = []
  let arrowStagger = 0
  ps.forEach((pt: any, i: number) => {
    const c = bandColor(i, 1)
    for (const a of (pt.annotations || [])) {
      if (a.type === 'box' && a.date1 && a.date2) {
        areaData.push([
          {
            xAxis: a.date1,
            itemStyle: { color: bandColor(i, 0.10), borderColor: bandColor(i, 0.6), borderWidth: 1, borderType: 'dashed' },
            label: { show: !!a.label, position: 'insideTop', color: c, fontSize: 10, formatter: a.label }
          },
          { xAxis: a.date2 }
        ])
      } else if (a.type === 'circle') {
        const target = a.grid === 'macd' ? macdPointData : pointData
        target.push({
          coord: [a.date, a.price], symbol: 'circle', symbolSize: 16,
          itemStyle: { color: 'transparent', borderColor: c, borderWidth: 2 },
          label: { show: !!a.label, position: 'top', color: c, fontSize: 10, formatter: a.label }
        })
      } else if (a.type === 'arrow') {
        const dy = 12 + arrowStagger * 18
        pointData.push({
          coord: [a.date, a.price], symbol: 'arrow', symbolSize: 12, symbolRotate: 180,
          symbolOffset: [0, -dy], itemStyle: { color: c },
          label: { show: true, position: 'top', color: c, fontSize: 10, formatter: a.label, offset: [0, -dy] }
        })
        arrowStagger++
      } else if (a.type === 'hline') {
        lineData.push({
          yAxis: a.price, lineStyle: { color: bandColor(i, 0.7), type: 'dashed', width: 1 },
          label: { show: !!a.label, position: 'insideEndTop', color: c, fontSize: 10, formatter: a.label }
        })
      }
    }
  })

  return {
    animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    legend: { data: ['MA5', 'MA10', 'MA20', 'MA30', 'MA60'], top: 0, itemHeight: 8, textStyle: { fontSize: 10 } },
    grid: [
      { left: 52, right: 16, top: 28, height: '48%' },
      { left: 52, right: 16, top: '62%', height: '14%' },
      { left: 52, right: 16, top: '80%', height: '16%' }
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, boundaryGap: true, axisLabel: { show: false } },
      { type: 'category', data: dates, gridIndex: 1, axisLabel: { show: false } },
      { type: 'category', data: dates, gridIndex: 2, axisLabel: { fontSize: 10 } }
    ],
    yAxis: [
      { scale: true, gridIndex: 0 },
      { gridIndex: 1, axisLabel: { show: false } },
      { gridIndex: 2, axisLabel: { fontSize: 10 } }
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2], start: 50, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1, 2], bottom: 0, height: 14, start: 50, end: 100 }
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: candles, xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: { color: up, color0: down, borderColor: up, borderColor0: down },
        markArea: { silent: true, data: areaData },
        markPoint: { silent: true, data: pointData },
        markLine: { silent: true, symbol: 'none', data: lineData }
      },
      ...maSeries,
      { name: '成交量', type: 'bar', data: p.volumes || [], xAxisIndex: 1, yAxisIndex: 1,
        itemStyle: { color: (params: any) => volColors[params.dataIndex] } },
      { name: 'MACD', type: 'bar', data: (p.macd?.hist || []), xAxisIndex: 2, yAxisIndex: 2,
        itemStyle: { color: (params: any) => (Number(params.data) >= 0 ? up : down) } },
      { name: 'DIF', type: 'line', data: (p.macd?.dif || []), xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: { color: '#e6a23c', width: 1 },
        markPoint: { silent: true, data: macdPointData } },
      { name: 'DEA', type: 'line', data: (p.macd?.dea || []), xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: { color: '#3b82f6', width: 1 } }
    ]
  }
}

const render = () => {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  if (hasData.value) chart.setOption(buildOption() as any, true)
}

onMounted(render)
watch(() => props.payload, render, { deep: true })
onUnmounted(() => { chart?.dispose(); chart = null })
</script>
