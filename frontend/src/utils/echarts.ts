import * as echarts from 'echarts/core'
import { BarChart, CandlestickChart, GaugeChart, LineChart, ScatterChart, TreemapChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import { LabelLayout } from 'echarts/features'
import { CanvasRenderer } from 'echarts/renderers'
import type { ECharts } from 'echarts/core'

echarts.use([
  // 按需引入时 labelLayout（hideOverlap 等）不注册就被静默忽略：板块轮动图的名字曾互相压字
  LabelLayout,
  BarChart,
  CandlestickChart,
  GaugeChart,
  LineChart,
  ScatterChart,
  TreemapChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TitleComponent,
  TooltipComponent,
  CanvasRenderer,
])

export { echarts }
export type { ECharts }
