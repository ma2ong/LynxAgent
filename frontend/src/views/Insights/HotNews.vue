<template>
  <div class="hot-news-page">
    <div class="page-head">
      <div>
        <h1>A股热点</h1>
        <p>只聚合 A 股市场相关热点、主题标签和事件方向，作为个股深研前的情报入口。</p>
      </div>
      <el-button type="primary" :loading="loading" @click="loadData">
        <el-icon><Refresh /></el-icon>
        刷新
      </el-button>
    </div>

    <section class="summary-band compact-summary">
      <div>
        <span>报告类型</span>
        <strong>{{ summary.report_type || '当前榜单' }}</strong>
      </div>
      <div>
        <span>新闻总数</span>
        <strong>{{ summary.news_total || 0 }} 条</strong>
      </div>
      <div>
        <span>热点新闻</span>
        <strong>{{ summary.hot_total || 0 }} 条</strong>
      </div>
      <div>
        <span>生成时间</span>
        <strong>{{ summary.generated_at || '-' }}</strong>
      </div>
    </section>

    <el-alert
      v-if="failedSources.length"
      class="source-alert"
      type="warning"
      :closable="false"
      show-icon
    >
      <template #title>
        部分外部源不可用：{{ failedSources.join('、') }}。当前使用 SaaS Lite 本地规则和量化数据降级展示。
      </template>
    </el-alert>

    <el-tabs v-model="activeTab" class="news-tabs">
      <el-tab-pane label="热榜" name="rank" />
      <el-tab-pane label="独立" name="independent" />
      <el-tab-pane label="AI分析" name="ai" />
    </el-tabs>

    <div class="content-grid">
      <section class="news-list">
        <div
          v-for="item in newsItems"
          :key="item.id"
          class="news-row"
          @click="openItem(item)"
        >
          <div class="rank">{{ item.rank }}</div>
          <div class="news-main">
            <div class="news-title">{{ item.title }}</div>
            <div class="news-meta">
              <el-tag size="small" :type="item.sentiment === '利好' ? 'danger' : 'info'">
                {{ item.sentiment }}
              </el-tag>
              <span>{{ item.sector }}</span>
              <span>{{ item.source }}</span>
              <span>{{ formatTime(item.publish_time) }}</span>
            </div>
          </div>
          <div class="score" :class="item.score >= 0.7 ? 'hot' : ''">
            {{ item.score.toFixed(2) }}
          </div>
        </div>
      </section>

      <aside class="side-panel">
        <div class="panel-block">
          <div class="panel-title">热点分类</div>
          <div class="tag-cloud">
            <el-tag
              v-for="item in categories"
              :key="item.name"
              effect="plain"
            >
              {{ item.name }} · {{ item.count }}
            </el-tag>
          </div>
        </div>

        <div class="panel-block">
          <div class="panel-title">AI解读</div>
          <p>
            当前榜单优先保留重组、订单、增持、业绩、审核进展等交易相关事件，
        过滤例行制度公告、低相关海外资讯和非 A 股噪音，再回到单股分析里验证趋势、量能和风险。
          </p>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { liteInsightsApi, type HotNewsItem } from '@/api/liteInsights'

const loading = ref(false)
const activeTab = ref('rank')
const summary = ref<Record<string, any>>({})
const newsItems = ref<HotNewsItem[]>([])
const categories = ref<Array<{ name: string; count: number }>>([])
const failedSources = ref<string[]>([])

const loadData = async () => {
  loading.value = true
  try {
    const res = await liteInsightsApi.getHotNews(60)
    const data = (res as any).data || {}
    summary.value = data.summary || {}
    newsItems.value = data.items || []
    categories.value = data.categories || []
    failedSources.value = data.failed_sources || []
  } catch (error: any) {
    console.error('加载热点新闻失败:', error)
    ElMessage.error(error?.message || '加载热点新闻失败')
  } finally {
    loading.value = false
  }
}

const formatTime = (value: string) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const openItem = (item: HotNewsItem) => {
  if (item.url) {
    window.open(item.url, '_blank')
  } else {
    ElMessage.info('Lite 模式当前展示聚合摘要，暂无外部详情链接')
  }
}

onMounted(loadData)
</script>

<style scoped lang="scss">
.hot-news-page {
  max-width: none;
  margin: 0 auto;
}

.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 8px;

  h1 {
    margin: 0 0 4px;
    font-size: 22px;
  }

  p {
    margin: 0;
    color: var(--el-text-color-secondary);
  }
}

.summary-band {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 8px;

  div {
    background: var(--el-bg-color);
    border: 1px solid var(--el-border-color-light);
    border-radius: 8px;
    padding: 10px 12px;
  }

  span {
    display: block;
    font-size: 12px;
    color: var(--el-text-color-secondary);
    margin-bottom: 4px;
  }

  strong {
    font-size: 16px;
    color: var(--el-text-color-primary);
  }
}

.source-alert {
  margin-bottom: 8px;
}

.news-tabs {
  margin-bottom: 6px;
}

.content-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 10px;
}

.news-list,
.side-panel {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
}

.news-row {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) 56px;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  cursor: pointer;
  border-bottom: 1px solid var(--el-border-color-lighter);

  &:last-child {
    border-bottom: none;
  }

  &:hover {
    background: var(--el-fill-color-lighter);
  }
}

.rank {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--el-fill-color-light);
  display: grid;
  place-items: center;
  font-weight: 700;
  color: var(--el-text-color-secondary);
}

.news-title {
  color: var(--el-color-primary);
  font-size: 14px;
  line-height: 1.35;
}

.news-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 5px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.score {
  font-weight: 700;
  text-align: right;
  color: var(--el-text-color-primary);

  &.hot {
    color: var(--el-color-danger);
  }
}

.side-panel {
  padding: 10px;
}

.panel-block + .panel-block {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.panel-title {
  font-weight: 700;
  margin-bottom: 10px;
}

.tag-cloud {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.panel-block p {
  color: var(--el-text-color-secondary);
  line-height: 1.7;
  margin: 0;
}

@media (max-width: 900px) {
  .summary-band,
  .content-grid {
    grid-template-columns: 1fr;
  }

  .page-head {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
