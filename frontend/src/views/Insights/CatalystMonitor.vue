<template>
  <div class="catalyst-page">
    <div class="page-head">
      <div>
        <h1>利好监控</h1>
        <p>围绕 A 股，把热点事件、涨跌幅、情绪和量化评分合成利好强度，用来发现值得进一步分析的标的。</p>
      </div>
      <div class="head-actions">
        <el-button :loading="loading" @click="loadData">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button type="primary" @click="goSingleAnalysis">
          <el-icon><TrendCharts /></el-icon>
          单股分析
        </el-button>
      </div>
    </div>

    <section class="control-band">
      <el-form label-position="top">
        <el-row :gutter="18">
          <el-col :xs="24" :sm="8">
            <el-form-item label="回溯窗口">
              <el-select v-model="windowValue">
                <el-option label="近 24 小时" value="24h" />
                <el-option label="近 3 天" value="3d" />
                <el-option label="近 7 天" value="7d" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="10">
            <el-form-item label="利好阈值">
              <el-slider v-model="threshold" :min="0" :max="12" :step="0.1" show-input />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="6">
            <el-form-item label="更新时间">
              <div class="updated-at">{{ updatedAt || '-' }}</div>
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
    </section>

    <div class="layout-grid">
      <section class="main-list">
        <div class="section-title">
          <span>Hot10 热点标的</span>
          <small>按利好强度、量化评分和情绪聚合</small>
        </div>

        <div class="stock-grid">
          <article
            v-for="(item, index) in topItems"
            :key="item.symbol"
            class="stock-card"
            @click="goStock(item.symbol)"
          >
            <div class="stock-card-head">
              <span class="rank">{{ index + 1 }}</span>
              <div>
                <strong>{{ item.name }}</strong>
                <span>{{ item.symbol }}</span>
              </div>
              <div class="quote">
                <strong>{{ item.price.toFixed(2) }}</strong>
                <em :class="item.change_percent >= 0 ? 'up' : 'down'">
                  {{ item.change_percent >= 0 ? '+' : '' }}{{ item.change_percent.toFixed(2) }}%
                </em>
              </div>
            </div>

            <svg class="sparkline" viewBox="0 0 160 44" preserveAspectRatio="none">
              <polyline
                :points="sparkPoints(item.sparkline)"
                :class="item.change_percent >= 0 ? 'line-up' : 'line-down'"
                fill="none"
                stroke-width="2"
              />
            </svg>

            <div class="metrics">
              <span>提及 {{ item.mentions }}</span>
              <span>利好强度 {{ item.hot_score.toFixed(2) }}</span>
              <span>评分 {{ item.score.toFixed(1) }}</span>
            </div>

            <div class="reasons">
              <el-tag v-for="reason in item.reasons" :key="reason" size="small" effect="plain">
                {{ reason }}
              </el-tag>
            </div>
          </article>
        </div>
      </section>

      <aside class="side-panel">
        <div class="section-title">
          <span>热点分类</span>
          <small>板块 / 行业 / 来源聚合</small>
        </div>

        <div v-for="(items, group) in categories" :key="group" class="category-group">
          <div class="category-title">{{ group }}</div>
          <div class="tag-cloud">
            <el-tag v-for="item in items" :key="`${group}-${item.name}`" effect="plain">
              {{ item.name }} · {{ item.count }}
            </el-tag>
          </div>
        </div>

        <div class="news-feed">
          <div class="section-title compact">
            <span>新闻流</span>
            <small>最新 {{ newsFeed.length }} 条</small>
          </div>
          <div v-for="item in newsFeed" :key="item.id" class="feed-item">
            <div class="feed-title">{{ item.title }}</div>
            <div class="feed-meta">
              <el-tag size="small" :type="item.sentiment === '利好' ? 'danger' : 'info'">{{ item.sentiment }}</el-tag>
              <span>{{ item.sector }}</span>
            </div>
          </div>
        </div>
      </aside>
    </div>

    <section class="realtime-table">
      <div class="section-title">
        <span>实时利好精选</span>
        <small>综合分 = 情绪 × 提及次数 × 时效加成</small>
      </div>
      <el-table :data="realtimePicks" empty-text="暂无满足阈值的利好新闻">
        <el-table-column type="index" label="#" width="56" />
        <el-table-column prop="symbol" label="标的" width="110" />
        <el-table-column prop="name" label="名称" width="130" />
        <el-table-column prop="change_percent" label="涨跌幅" width="110">
          <template #default="{ row }">
            <span :class="row.change_percent >= 0 ? 'up' : 'down'">
              {{ row.change_percent >= 0 ? '+' : '' }}{{ row.change_percent.toFixed(2) }}%
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="hot_score" label="利好强度" width="120" />
        <el-table-column prop="score" label="量化评分" width="120" />
        <el-table-column label="标题">
          <template #default="{ row }">
            {{ row.reasons.join('、') }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button link type="primary" @click="goStock(row.symbol)">分析</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, TrendCharts } from '@element-plus/icons-vue'
import { liteInsightsApi, type CatalystItem, type HotNewsItem } from '@/api/liteInsights'

const router = useRouter()
const loading = ref(false)
const windowValue = ref('24h')
const threshold = ref(1.5)
const updatedAt = ref('')
const topItems = ref<CatalystItem[]>([])
const realtimePicks = ref<CatalystItem[]>([])
const categories = ref<Record<string, Array<{ name: string; count: number }>>>({})
const newsFeed = ref<HotNewsItem[]>([])

let reloadTimer: number | undefined

const loadData = async () => {
  loading.value = true
  try {
    const res = await liteInsightsApi.getCatalysts({
      window: windowValue.value,
      threshold: threshold.value,
      limit: 10
    })
    const data = (res as any).data || {}
    updatedAt.value = data.updated_at || ''
    topItems.value = data.top_items || []
    realtimePicks.value = data.realtime_picks || []
    categories.value = data.categories || {}
    newsFeed.value = data.news_feed || []
  } catch (error: any) {
    console.error('加载利好监控失败:', error)
    ElMessage.error(error?.message || '加载利好监控失败')
  } finally {
    loading.value = false
  }
}

const sparkPoints = (values: number[]) => {
  if (!values.length) return ''
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  return values.map((value, index) => {
    const x = (index / Math.max(1, values.length - 1)) * 160
    const y = 40 - ((value - min) / range) * 34
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}

const goStock = (symbol: string) => {
  router.push({ path: '/analysis/single', query: { stock: symbol } })
}

const goSingleAnalysis = () => {
  router.push('/analysis/single')
}

watch([windowValue, threshold], () => {
  window.clearTimeout(reloadTimer)
  reloadTimer = window.setTimeout(loadData, 300)
})

onMounted(loadData)
</script>

<style scoped lang="scss">
.catalyst-page {
  max-width: none;
  margin: 0 auto;
}

.page-head,
.section-title,
.stock-card-head,
.metrics,
.feed-meta {
  display: flex;
  align-items: center;
}

.page-head {
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;

  h1 {
    margin: 0 0 4px;
    font-size: 22px;
  }

  p {
    margin: 0;
    color: var(--el-text-color-secondary);
  }
}

.head-actions {
  display: flex;
  gap: 10px;
}

.control-band,
.main-list,
.side-panel,
.realtime-table {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
}

.control-band {
  padding: 12px 14px 0;
  margin-bottom: 10px;
}

.updated-at {
  min-height: 32px;
  line-height: 32px;
  color: var(--el-text-color-secondary);
}

.layout-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 10px;
  align-items: start;
}

.main-list,
.side-panel,
.realtime-table {
  padding: 12px;
}

.section-title {
  justify-content: space-between;
  margin-bottom: 10px;

  span {
    font-weight: 700;
    font-size: 16px;
  }

  small {
    color: var(--el-text-color-secondary);
  }

  &.compact {
    margin-top: 12px;
  }
}

.stock-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.stock-card {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 12px;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;

  &:hover {
    border-color: var(--el-color-primary);
    box-shadow: 0 6px 18px rgba(30, 64, 175, 0.08);
  }
}

.stock-card-head {
  gap: 12px;

  .rank {
    width: 34px;
    height: 34px;
    border-radius: 8px;
    display: grid;
    place-items: center;
    background: var(--el-fill-color-light);
    font-weight: 700;
  }

  strong,
  span {
    display: block;
  }

  span {
    color: var(--el-text-color-secondary);
    font-size: 13px;
    margin-top: 2px;
  }
}

.quote {
  margin-left: auto;
  text-align: right;

  em {
    display: inline-block;
    margin-top: 4px;
    padding: 2px 8px;
    border-radius: 5px;
    color: #fff;
    font-style: normal;
    font-size: 12px;
  }
}

.sparkline {
  width: 100%;
  height: 54px;
  margin: 12px 0 8px;
  background: linear-gradient(180deg, transparent, var(--el-fill-color-lighter));
}

.line-up {
  stroke: #ef4444;
}

.line-down {
  stroke: #16a34a;
}

.metrics {
  justify-content: space-between;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  gap: 8px;
}

.reasons,
.tag-cloud {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.reasons {
  margin-top: 12px;
}

.category-group + .category-group {
  margin-top: 20px;
}

.category-title {
  font-weight: 600;
  margin-bottom: 10px;
}

.news-feed {
  border-top: 1px solid var(--el-border-color-lighter);
  margin-top: 22px;
}

.feed-item {
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.feed-title {
  line-height: 1.5;
  margin-bottom: 8px;
}

.feed-meta {
  gap: 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.realtime-table {
  margin-top: 18px;
}

.up {
  color: #ef4444;
}

.quote .up {
  background: #ef4444;
  color: #fff;
}

.down {
  color: #16a34a;
}

.quote .down {
  background: #16a34a;
  color: #fff;
}

@media (max-width: 1100px) {
  .layout-grid,
  .stock-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .page-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .head-actions {
    width: 100%;

    .el-button {
      flex: 1;
    }
  }
}
</style>
