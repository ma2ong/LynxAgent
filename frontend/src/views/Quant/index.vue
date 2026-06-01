<template>
  <div class="quant-page">
    <section class="page-head">
      <div>
        <h1>智能选股</h1>
        <p>自动横向比较全市场 A 股，优先输出短中期更值得跟踪的量化推荐池</p>
      </div>
      <el-tag effect="plain" type="info">研究与模拟，不直接下单</el-tag>
    </section>

    <el-tabs v-model="activeTab" class="quant-tabs" @tab-change="onTabChange">
      <el-tab-pane label="一键推荐" name="screen">
        <div class="smart-home">
          <section class="smart-hero compact-smart-hero">
            <div>
              <h2>一键智能推荐股票池</h2>
              <p>系统自动综合量化分、趋势、动量、RSI、均线结构、突破信号、成交活跃度、预测因子和风险控制，直接生成当前更值得跟踪的候选股票。</p>
            </div>
            <div class="smart-inline-settings">
              <label>
                <span>候选</span>
                <el-input-number v-model="smartPoolForm.universe_limit" :min="50" :max="5000" :step="50" controls-position="right" />
              </label>
              <label>
                <span>推荐</span>
                <el-input-number v-model="smartPoolForm.limit" :min="5" :max="50" controls-position="right" />
              </label>
            </div>
            <div class="smart-actions">
              <el-button type="success" size="large" native-type="button" :loading="smartPoolLoading" @click="loadSmartPool">
                <el-icon><Search /></el-icon>
                一键智能推荐
              </el-button>
              <span v-if="smartPoolResult?.items.length">已推荐 {{ smartPoolResult.items.length }} 只</span>
            </div>
          </section>

          <section class="result-panel smart-result-panel">
            <div v-if="smartPoolResult?.items.length" class="mini-summary">
              <span>自动候选 {{ smartPoolResult.universe_size }} 只</span>
              <b>推荐 {{ smartPoolResult.items.length }} 只</b>
              <span v-if="smartPoolResult.analyzed">已分析 {{ smartPoolResult.analyzed }} 只</span>
              <div class="table-actions">
                <el-button size="small" @click="toggleSmartSelection">全选/取消</el-button>
                <el-button size="small" type="success" :disabled="!selectedSmartRows.length" @click="addSelectedToFavorites">
                  加入自选({{ selectedSmartRows.length }})
                </el-button>
                <el-button size="small" type="primary" :disabled="!selectedSmartRows.length" @click="batchAnalyzeSelected">
                  批量分析
                </el-button>
                <el-button size="small" type="success" plain @click="addAllSmartToFavorites">
                  一键全选加入自选
                </el-button>
              </div>
            </div>
            <el-table
              v-if="smartPoolResult?.items.length"
              ref="smartTableRef"
              :data="smartPoolResult.items"
              max-height="520"
              size="small"
              @selection-change="handleSmartSelectionChange"
            >
              <el-table-column type="selection" width="44" fixed />
              <el-table-column label="量化分" width="90" fixed>
                <template #default="{ row }"><b>{{ displayScore(row) }}</b></template>
              </el-table-column>
              <el-table-column prop="symbol" label="代码" width="100" />
              <el-table-column prop="name" label="名称" width="120" />
              <el-table-column label="行业/板块" width="130">
                <template #default="{ row }">{{ row.industry || row.board || '-' }}</template>
              </el-table-column>
              <el-table-column label="现价" width="90"><template #default="{ row }">{{ formatNumber(row.close) }}</template></el-table-column>
              <el-table-column label="涨跌幅" width="90" align="right">
                <template #default="{ row }">
                  <span :class="changeClass(row.pct_chg)">{{ signedPercent(row.pct_chg) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="入选理由" min-width="460">
                <template #default="{ row }">
                  <el-tag v-for="reason in row.reasons" :key="reason" class="capability-tag" effect="plain">
                    {{ reason }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="160" fixed="right">
                <template #default="{ row }">
                  <el-button type="primary" link size="small" @click="addOneToFavorites(row)">加入自选</el-button>
                  <el-button link type="primary" size="small" @click="openChart(row)">看图</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else description="点击一键智能推荐后生成量化股票池" />
          </section>

          <el-collapse class="advanced-tools">
            <el-collapse-item title="高级手动选股工具" name="manual-screen">
              <div class="tool-layout">
                <el-form class="control-panel" label-position="top" @submit.prevent>
                  <el-form-item label="股票池">
                    <el-input v-model="screenSymbolsText" type="textarea" :rows="8" placeholder="每行一个代码，或用逗号分隔" />
                  </el-form-item>
                  <el-form-item label="返回数量">
                    <el-input-number v-model="screenForm.limit" :min="1" :max="200" style="width: 100%" />
                  </el-form-item>
                  <el-button type="primary" native-type="button" :loading="screenLoading" @click="runScreen">
                    <el-icon><Search /></el-icon>
                    手动选股
                  </el-button>
                </el-form>
                <section class="result-panel">
                  <el-table v-if="screenResult?.items.length" :data="screenResult.items" height="420">
                    <el-table-column prop="symbol" label="代码" min-width="110" fixed />
                    <el-table-column label="综合分" min-width="110"><template #default="{ row }"><b>{{ row.score.toFixed(1) }}</b></template></el-table-column>
                    <el-table-column label="信号" min-width="110"><template #default="{ row }"><el-tag :type="signalTagType(row.signal)">{{ signalText(row.signal) }}</el-tag></template></el-table-column>
                    <el-table-column label="趋势" min-width="90"><template #default="{ row }">{{ row.factors.trend.toFixed(1) }}</template></el-table-column>
                    <el-table-column label="动量" min-width="90"><template #default="{ row }">{{ row.factors.momentum.toFixed(1) }}</template></el-table-column>
                    <el-table-column label="风控" min-width="90"><template #default="{ row }">{{ row.factors.risk_control.toFixed(1) }}</template></el-table-column>
                    <el-table-column label="回撤" min-width="100"><template #default="{ row }">{{ percent(row.risk.max_drawdown) }}</template></el-table-column>
                  </el-table>
                  <el-empty v-else description="输入股票池后可手动选股" />
                </section>
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>
      </el-tab-pane>


      <el-tab-pane label="形态智选" name="patterns">
        <div class="smart-home">
          <section class="smart-hero compact-smart-hero">
            <div>
              <h2>7类拉升前图形扫描</h2>
              <p>全市场扫描A股K线结构，识别均线粘合、地量启动、挖坑修复、压力试盘、MACD修复、小步快跑和盘口代理信号。</p>
            </div>
            <div class="smart-inline-settings">
              <label>
                <span>候选</span>
                <el-input-number v-model="patternPoolForm.universe_limit" :min="50" :max="5000" :step="50" controls-position="right" />
              </label>
              <label>
                <span>返回</span>
                <el-input-number v-model="patternPoolForm.limit" :min="5" :max="50" controls-position="right" />
              </label>
              <label>
                <span>形态阈值</span>
                <el-input-number v-model="patternPoolForm.min_strength" :min="50" :max="95" :step="5" controls-position="right" />
              </label>
            </div>
            <div class="smart-actions">
              <el-button type="success" size="large" native-type="button" :loading="patternPoolLoading" @click="loadPatternPool">
                <el-icon><Search /></el-icon>
                一键扫描形态
              </el-button>
              <span v-if="patternPoolResult?.items.length">已命中 {{ patternPoolResult.items.length }} 只</span>
            </div>
          </section>

          <section class="result-panel smart-result-panel">
            <div v-if="patternPoolResult?.items.length" class="mini-summary">
              <span>自动候选 {{ patternPoolResult.universe_size }} 只</span>
              <b>命中 {{ patternPoolResult.matched || patternPoolResult.items.length }} 只</b>
              <span v-if="patternPoolResult.analyzed">已分析 {{ patternPoolResult.analyzed }} 只</span>
              <span v-if="patternPoolResult?.excluded">已排除 {{ patternPoolResult.excluded }} 只</span>
              <span v-if="patternPoolResult?.source === 'live-fallback'" style="color:#e6a23c">本地数据未就绪，请先到「数据同步」做全量同步以扫描全市场</span>
              <div class="table-actions">
                <el-button size="small" @click="togglePatternSelection">全选/取消</el-button>
                <el-button size="small" type="success" :disabled="!selectedPatternRows.length" @click="addSelectedPatternsToFavorites">
                  加入自选({{ selectedPatternRows.length }})
                </el-button>
                <el-button size="small" type="primary" :disabled="!selectedPatternRows.length" @click="batchAnalyzePatternSelected">
                  批量分析
                </el-button>
                <el-button size="small" type="success" plain @click="addAllPatternsToFavorites">
                  一键全选加入自选
                </el-button>
              </div>
            </div>
            <el-table
              v-if="patternPoolResult?.items.length"
              ref="patternTableRef"
              :data="patternPoolResult.items"
              max-height="520"
              size="small"
              @selection-change="handlePatternSelectionChange"
            >
              <el-table-column type="selection" width="44" fixed />
              <el-table-column label="形态分" width="90" fixed>
                <template #default="{ row }"><b>{{ formatScore(row.pattern_score ?? row.score) }}</b></template>
              </el-table-column>
              <el-table-column prop="symbol" label="代码" width="100" />
              <el-table-column prop="name" label="名称" width="120" />
              <el-table-column label="行业/板块" width="140">
                <template #default="{ row }">{{ row.industry || row.board || '-' }}</template>
              </el-table-column>
              <el-table-column label="现价" width="90"><template #default="{ row }">{{ formatNumber(row.close) }}</template></el-table-column>
              <el-table-column label="涨跌幅" width="90" align="right">
                <template #default="{ row }">
                  <span :class="changeClass(row.pct_chg)">{{ signedPercent(row.pct_chg) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="命中形态" min-width="320">
                <template #default="{ row }">
                  <el-tag
                    v-for="pattern in row.matched_patterns || row.patterns || []"
                    :key="pattern.key"
                    class="capability-tag"
                    type="primary"
                    effect="plain"
                  >
                    {{ pattern.name }} {{ formatScore(pattern.strength) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="证据/理由" min-width="360">
                <template #default="{ row }">
                  <el-tag v-for="reason in row.reasons" :key="reason" class="capability-tag" effect="plain">
                    {{ reason }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="160" fixed="right">
                <template #default="{ row }">
                  <el-button type="primary" link size="small" @click="addOneToFavorites(row)">加入自选</el-button>
                  <el-button link type="primary" size="small" @click="openChart(row)">看图</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else description="点击一键扫描形态，自动找出符合7类拉升前结构的股票" />
          </section>
        </div>
      </el-tab-pane>

      <el-tab-pane label="数据同步" name="lake">
        <div class="sync-bar" style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px;">
          <el-button type="primary" :loading="syncRunning" @click="startSync(true)">全量同步</el-button>
          <el-button :loading="syncRunning" @click="startSync(false)">增量同步</el-button>
          <span v-if="syncStatus.total">进度 {{ syncStatus.done }}/{{ syncStatus.total }}（{{ syncStatus.phase }}）失败 {{ syncStatus.errors_count }}</span>
          <el-progress v-if="syncStatus.total" :percentage="syncPct" style="flex:1;min-width:200px;" />
        </div>
        <div class="tool-layout">
          <el-form class="control-panel" label-position="top" @submit.prevent>
            <el-form-item label="股票池数量">
              <el-input-number v-model="poolLimit" :min="1" :max="5000" style="width: 100%" />
            </el-form-item>
            <el-button native-type="button" :loading="poolLoading" @click="loadPool">读取股票池</el-button>
            <el-button type="primary" native-type="button" :loading="syncLoading" @click="syncLake">同步 AKShare 数据湖</el-button>
          </el-form>
          <section class="result-panel">
            <div v-if="syncResult" class="mini-summary">
              <span>集合 {{ syncResult.collection }}</span>
              <b>{{ syncResult.total }}</b>
              <span>新增 {{ syncResult.inserted }} / 更新 {{ syncResult.updated }}</span>
            </div>
            <el-table v-if="poolResult?.items.length" :data="poolResult.items" height="520">
              <el-table-column prop="symbol" label="代码" min-width="110" fixed />
              <el-table-column prop="name" label="名称" min-width="140" />
              <el-table-column prop="market" label="市场" min-width="100" />
              <el-table-column prop="source" label="来源" min-width="120" />
            </el-table>
            <el-empty v-else description="读取或同步股票池后显示数据" />
          </section>
        </div>
      </el-tab-pane>

      <el-tab-pane label="单股量化" name="analysis">
        <div class="tool-layout">
          <el-form class="control-panel" label-position="top" @submit.prevent>
            <el-form-item label="股票代码">
              <el-input v-model="analysisForm.symbol" placeholder="600519 / 000001 / 300750" clearable />
            </el-form-item>
            <el-form-item label="日期范围">
              <el-date-picker v-model="analysisRange" type="daterange" start-placeholder="开始日期" end-placeholder="结束日期" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
            <el-button type="primary" native-type="button" :loading="analysisLoading" @click="runAnalysis">
              <el-icon><TrendCharts /></el-icon>
              生成画像
            </el-button>
          </el-form>

          <section class="result-panel">
            <el-empty v-if="!analysisResult" description="输入代码后生成量化画像" />
            <template v-else>
              <div class="score-row">
                <div class="score-block">
                  <span>{{ analysisResult.symbol }}</span>
                  <strong>{{ analysisResult.score.toFixed(1) }}</strong>
                  <el-tag :type="signalTagType(analysisResult.signal)">{{ signalText(analysisResult.signal) }}</el-tag>
                </div>
                <div class="latest-grid">
                  <span>日期 {{ analysisResult.latest.date }}</span>
                  <span>收盘 {{ formatNumber(analysisResult.latest.close) }}</span>
                  <span>成交额 {{ formatMoney(analysisResult.latest.amount) }}</span>
                </div>
              </div>

              <div class="factor-grid">
                <div v-for="(value, key) in analysisResult.factors" :key="key" class="factor-item">
                  <div class="factor-title">
                    <span>{{ factorLabel(key) }}</span>
                    <b>{{ value.toFixed(1) }}</b>
                  </div>
                  <el-progress :percentage="Math.round(value)" :stroke-width="10" />
                </div>
              </div>

              <div class="metric-grid">
                <div><span>年化波动</span><b>{{ percent(analysisResult.risk.volatility) }}</b></div>
                <div><span>最大回撤</span><b>{{ percent(analysisResult.risk.max_drawdown) }}</b></div>
                <div><span>夏普</span><b>{{ analysisResult.risk.sharpe.toFixed(2) }}</b></div>
              </div>

              <div v-if="analysisPatterns.length || analysisForecast" class="integration-grid">
                <section v-if="analysisPatterns.length" class="integration-card">
                  <h3>形态识别</h3>
                  <el-tag v-for="pattern in analysisPatterns" :key="pattern.key" type="primary" effect="plain">
                    {{ pattern.name }} {{ pattern.strength.toFixed(1) }}
                  </el-tag>
                </section>
                <section v-if="analysisForecast" class="integration-card">
                  <h3>Kronos 风格预测</h3>
                  <div class="metric-grid compact">
                    <div><span>趋势分</span><b>{{ analysisForecast.trend_score.toFixed(1) }}</b></div>
                    <div><span>上行概率</span><b>{{ percent(analysisForecast.upside_probability) }}</b></div>
                    <div><span>预期收益</span><b>{{ percent(analysisForecast.expected_return) }}</b></div>
                  </div>
                </section>
              </div>
            </template>
          </section>
        </div>
      </el-tab-pane>

      <el-tab-pane label="策略回测" name="backtest">
        <div class="tool-layout">
          <el-form class="control-panel" label-position="top" @submit.prevent>
            <el-form-item label="股票代码">
              <el-input v-model="backtestForm.symbol" placeholder="600519" clearable />
            </el-form-item>
            <el-form-item label="策略">
              <el-select v-model="backtestForm.strategy" style="width: 100%">
                <el-option label="均线放量" value="ma_volume" />
                <el-option label="海龟突破" value="turtle_breakout" />
                <el-option label="RPS 突破" value="rps_breakout" />
                <el-option label="高窄旗形" value="high_tight_flag" />
                <el-option label="涨停洗盘修复" value="limit_up_washout" />
                <el-option label="多均线共振突破" value="multi_ma_breakout" />
              </el-select>
            </el-form-item>
            <el-form-item label="回测引擎">
              <el-select v-model="backtestForm.engine" style="width: 100%">
                <el-option label="Vector 快速回测" value="vector" />
                <el-option label="Backtrader 成熟引擎" value="backtrader" />
                <el-option label="AKQuant 适配器" value="akquant" />
              </el-select>
            </el-form-item>
            <el-form-item label="初始资金">
              <el-input-number v-model="backtestForm.initial_cash" :min="1000" :step="10000" style="width: 100%" />
            </el-form-item>
            <el-form-item label="日期范围">
              <el-date-picker v-model="backtestRange" type="daterange" start-placeholder="开始日期" end-placeholder="结束日期" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
            <el-button type="primary" native-type="button" :loading="backtestLoading" @click="runBacktest">
              <el-icon><DataLine /></el-icon>
              运行回测
            </el-button>
          </el-form>

          <section class="result-panel">
            <el-empty v-if="!backtestResult" description="选择策略后运行回测" />
            <template v-else>
              <div class="mini-summary">
                <span>引擎</span>
                <b>{{ backtestResult.engine }}</b>
              </div>
              <div class="metric-grid">
                <div><span>最终权益</span><b>{{ formatMoney(backtestResult.final_value) }}</b></div>
                <div><span>总收益</span><b>{{ percent(backtestResult.total_return) }}</b></div>
                <div><span>年化收益</span><b>{{ percent(backtestResult.annualized_return) }}</b></div>
                <div><span>最大回撤</span><b>{{ percent(backtestResult.max_drawdown) }}</b></div>
                <div><span>胜率</span><b>{{ percent(backtestResult.win_rate) }}</b></div>
                <div><span>交易次数</span><b>{{ backtestResult.trades }}</b></div>
              </div>
              <div class="equity-chart">
                <div v-for="point in normalizedEquity" :key="point.date" class="equity-bar" :style="{ height: `${point.height}%` }" :title="`${point.date}: ${formatMoney(point.equity)}`"></div>
              </div>
            </template>
          </section>
        </div>
      </el-tab-pane>

      <el-tab-pane label="因子研究" name="research">
        <div class="tool-layout">
          <el-form class="control-panel" label-position="top" @submit.prevent>
            <el-form-item label="研究股票池">
              <el-input v-model="researchSymbolsText" type="textarea" :rows="8" placeholder="每行一个代码，最多 50 个" />
            </el-form-item>
            <el-form-item label="初始资金">
              <el-input-number v-model="researchCash" :min="1000" :step="10000" style="width: 100%" />
            </el-form-item>
            <el-button type="primary" native-type="button" :loading="researchLoading" @click="runResearch">
              运行因子研究
            </el-button>
          </el-form>

          <section class="result-panel">
            <el-table v-if="researchResult?.candidates.length" :data="researchResult.candidates" height="520">
              <el-table-column prop="name" label="因子" min-width="160" fixed />
              <el-table-column prop="score" label="研究分" min-width="100" />
              <el-table-column label="平均收益" min-width="110"><template #default="{ row }">{{ percent(row.avg_return) }}</template></el-table-column>
              <el-table-column label="平均回撤" min-width="110"><template #default="{ row }">{{ percent(row.avg_max_drawdown) }}</template></el-table-column>
              <el-table-column label="平均胜率" min-width="110"><template #default="{ row }">{{ percent(row.avg_win_rate) }}</template></el-table-column>
              <el-table-column prop="sample_size" label="样本" min-width="80" />
              <el-table-column prop="hypothesis" label="假设" min-width="320" />
            </el-table>
            <el-empty v-else description="运行后显示候选因子排名" />
          </section>
        </div>
      </el-tab-pane>

    </el-tabs>

    <el-drawer v-model="chartDrawer" :title="chartTitle" size="62%" direction="rtl">
      <div v-loading="chartLoading">
        <KLineProChart v-if="chartPayload" :payload="chartPayload" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { DataLine, Search, TrendCharts } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { favoritesApi } from '@/api/favorites'
import KLineProChart from '@/components/KLineProChart.vue'
import {
  quantApi,
  type BacktestResult,
  type DataLakeSyncResult,
  type FactorResearchResult,
  type ForecastResult,
  type PatternRecognitionResult,
  type QuantAnalysisResult,
  type QuantPatternPoolItem,
  type QuantPatternPoolResult,
  type QuantScreenResult,
  type QuantSmartPoolItem,
  type QuantSmartPoolResult,
  type QuantStockPoolResult
} from '@/api/quant'

const activeTab = ref('screen')
const tabLoaded = ref<Record<string, boolean>>({ screen: false })

const onTabChange = (tabName: string) => {
  // placeholder: tabs that need auto-loading on first switch go here
  // e.g.: if (!tabLoaded.value[tabName]) { loadTabData(tabName); tabLoaded.value[tabName] = true }
}

const router = useRouter()

const poolLimit = ref(200)
const poolLoading = ref(false)
const syncLoading = ref(false)
const poolResult = ref<QuantStockPoolResult | null>(null)
const syncResult = ref<DataLakeSyncResult | null>(null)

const analysisForm = ref({ symbol: '600519' })
const analysisRange = ref<[string, string] | null>(null)
const analysisLoading = ref(false)
const analysisResult = ref<QuantAnalysisResult | null>(null)

const screenSymbolsText = ref('600519\n000001\n300750')
const screenForm = ref({ limit: 30 })
const screenLoading = ref(false)
const screenResult = ref<QuantScreenResult | null>(null)
const smartPoolForm = ref({ limit: 20, universe_limit: 5000 })
const smartPoolLoading = ref(false)
const smartPoolResult = ref<QuantSmartPoolResult | null>(null)
const smartTableRef = ref<any>()
const selectedSmartRows = ref<QuantSmartPoolItem[]>([])
const patternPoolForm = ref({ limit: 20, universe_limit: 5000, min_strength: 70, exclude_fundamental: true })
const patternPoolLoading = ref(false)
const patternPoolResult = ref<QuantPatternPoolResult | null>(null)
const patternTableRef = ref<any>()
const selectedPatternRows = ref<QuantPatternPoolItem[]>([])

const backtestForm = ref({
  symbol: '600519',
  strategy: 'ma_volume',
  engine: 'vector',
  initial_cash: 100000
})
const backtestRange = ref<[string, string] | null>(null)
const backtestLoading = ref(false)
const backtestResult = ref<BacktestResult | null>(null)

const researchSymbolsText = ref('600519\n000001\n300750')
const researchCash = ref(100000)
const researchLoading = ref(false)
const researchResult = ref<FactorResearchResult | null>(null)

const analysisForecast = computed<ForecastResult | null>(() => (
  (analysisResult.value?.integrations?.kronos_forecast as ForecastResult | undefined) || null
))
const analysisPatterns = computed<PatternRecognitionResult['patterns']>(() => (
  ((analysisResult.value?.integrations?.pattern_recognition as PatternRecognitionResult | undefined)?.patterns || [])
))

const normalizedEquity = computed(() => {
  const curve = backtestResult.value?.equity_curve || []
  if (!curve.length) return []
  const values = curve.map((item) => item.equity)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = Math.max(max - min, 1)
  return curve.map((item) => ({ ...item, height: 16 + ((item.equity - min) / range) * 84 }))
})

const parseRange = (range: [string, string] | null) => ({ start_date: range?.[0], end_date: range?.[1] })

const parseSymbols = (text: string) =>
  text
    .split(/[\n,\s，]+/)
    .map((item) => item.trim())
    .filter(Boolean)

const syncStatus = ref<any>({ running: false, phase: 'idle', done: 0, total: 0, errors_count: 0 })
const syncRunning = computed(() => !!syncStatus.value.running)
const syncPct = computed(() => syncStatus.value.total ? Math.floor(syncStatus.value.done / syncStatus.value.total * 100) : 0)
let syncTimer: number | undefined
const pollSync = async () => {
  syncStatus.value = await quantApi.syncStatus()
  if (syncStatus.value.running) {
    syncTimer = window.setTimeout(pollSync, 2500)
  }
}
const startSync = async (full: boolean) => {
  await quantApi.syncMarket(full)
  pollSync()
}
onMounted(() => { quantApi.syncStatus().then(s => { syncStatus.value = s; if (s.running) pollSync() }) })
onUnmounted(() => { if (syncTimer) window.clearTimeout(syncTimer) })

const loadPool = async () => {
  poolLoading.value = true
  try {
    poolResult.value = await quantApi.pool(poolLimit.value)
    const n = poolResult.value?.items?.length || 0
    if (n) ElMessage.success(`已读取股票池 ${n} 只`)
    else ElMessage.warning('股票池为空，请检查数据源或稍后重试')
  } catch (error: any) {
    ElMessage.error(error?.message || '读取股票池失败')
  } finally {
    poolLoading.value = false
  }
}

const syncLake = async () => {
  syncLoading.value = true
  try {
    const res = await quantApi.syncDataLake({ limit: poolLimit.value })
    syncResult.value = res
    poolResult.value = await quantApi.pool(poolLimit.value)
    const errs = (res as any)?.errors || []
    if (errs.length) {
      ElMessage.warning(`已拉取实时股票池 ${res?.total ?? 0} 只，但未持久化：${errs[0]}`)
    } else {
      ElMessage.success(`数据湖同步完成：新增 ${res?.inserted ?? 0} / 更新 ${res?.updated ?? 0}`)
    }
  } catch (error: any) {
    ElMessage.error(error?.message || '同步数据湖失败')
  } finally {
    syncLoading.value = false
  }
}

const runAnalysis = async () => {
  if (!analysisForm.value.symbol.trim()) {
    ElMessage.warning('请输入股票代码')
    return
  }
  analysisLoading.value = true
  try {
    analysisResult.value = await quantApi.analyze({ symbol: analysisForm.value.symbol.trim(), ...parseRange(analysisRange.value) })
  } finally {
    analysisLoading.value = false
  }
}

const runScreen = async () => {
  const symbols = parseSymbols(screenSymbolsText.value)
  if (!symbols.length) {
    ElMessage.warning('请输入股票池')
    return
  }
  screenLoading.value = true
  try {
    screenResult.value = await quantApi.screen({ symbols, limit: screenForm.value.limit })
    smartPoolResult.value = null
  } finally {
    screenLoading.value = false
  }
}

const loadSmartPool = async () => {
  smartPoolLoading.value = true
  try {
    smartPoolResult.value = await quantApi.smartPool(smartPoolForm.value.limit, smartPoolForm.value.universe_limit)
    screenResult.value = null
    selectedSmartRows.value = []
  } finally {
    smartPoolLoading.value = false
  }
}

const handleSmartSelectionChange = (rows: QuantSmartPoolItem[]) => {
  selectedSmartRows.value = rows
}

const formatScore = (value?: number | null) => Number(value ?? 0).toFixed(1)

const displayScore = (row: QuantSmartPoolItem) => formatScore(row.quant_score ?? row.score)

const loadPatternPool = async () => {
    patternPoolLoading.value = true
    try {
        patternPoolResult.value = await quantApi.patternPool(
            patternPoolForm.value.limit,
            patternPoolForm.value.universe_limit,
            patternPoolForm.value.min_strength
        )
        selectedPatternRows.value = []
    } catch (error: any) {
        ElMessage.error(error?.message || '形态扫描失败，请重试')
        patternPoolResult.value = null
    } finally {
        patternPoolLoading.value = false
    }
    }

const handlePatternSelectionChange = (rows: QuantPatternPoolItem[]) => {
  selectedPatternRows.value = rows
}

const addFavoriteRows = async (rows: QuantSmartPoolItem[]) => {
  const uniqueRows = rows.filter((row, index, arr) =>
    row?.symbol && arr.findIndex(item => item.symbol === row.symbol) === index
  )
  if (!uniqueRows.length) {
    ElMessage.warning('请选择股票')
    return
  }
  const results = await Promise.allSettled(
    uniqueRows.map(row =>
      favoritesApi.add({
        symbol: row.symbol,
        stock_code: row.symbol,
        stock_name: row.name || row.symbol,
        market: row.market || 'A股',
        notes: `智能选股推荐，量化分 ${displayScore(row)}，行业/板块：${row.industry || row.board || '-'}`
      })
    )
  )
  results.forEach((r, i) => {
    if (r.status === 'rejected') console.warn('加入自选失败', uniqueRows[i]?.symbol, r.reason)
  })
  const successCount = results.filter(r => r.status === 'fulfilled').length
  if (successCount) {
    ElMessage.success(`已加入自选 ${successCount} 只`)
  } else {
    ElMessage.error('加入自选失败')
  }
}

const addOneToFavorites = (row: QuantSmartPoolItem) => addFavoriteRows([row])
const addSelectedToFavorites = () => addFavoriteRows(selectedSmartRows.value)
const addAllSmartToFavorites = () => addFavoriteRows(smartPoolResult.value?.items || [])
const addSelectedPatternsToFavorites = () => addFavoriteRows(selectedPatternRows.value)
const addAllPatternsToFavorites = () => addFavoriteRows(patternPoolResult.value?.items || [])

const toggleSmartSelection = () => {
  smartTableRef.value?.toggleAllSelection?.()
}

const togglePatternSelection = () => {
  patternTableRef.value?.toggleAllSelection?.()
}

const batchAnalyzeSelected = () => {
  const symbols = selectedSmartRows.value.map(row => row.symbol).filter(Boolean)
  if (!symbols.length) {
    ElMessage.warning('请选择要批量分析的股票')
    return
  }
  router.push({ path: '/analysis/batch', query: { stocks: symbols.join(','), market: 'A股' } })
}


    const batchAnalyzePatternSelected = () => {
    const symbols = selectedPatternRows.value.map(row => row.symbol).filter(Boolean)
    if (!symbols.length) {
        ElMessage.warning('请选择要批量分析的股票')
        return
    }
    router.push({ path: '/analysis/batch', query: { stocks: symbols.join(','), market: 'A股' } })
    }

const runBacktest = async () => {
  if (!backtestForm.value.symbol.trim()) {
    ElMessage.warning('请输入股票代码')
    return
  }
  backtestLoading.value = true
  try {
    backtestResult.value = await quantApi.backtest({
      symbol: backtestForm.value.symbol.trim(),
      strategy: backtestForm.value.strategy,
      engine: backtestForm.value.engine,
      initial_cash: backtestForm.value.initial_cash,
      ...parseRange(backtestRange.value)
    })
  } finally {
    backtestLoading.value = false
  }
}

const runResearch = async () => {
  const symbols = parseSymbols(researchSymbolsText.value)
  if (!symbols.length) {
    ElMessage.warning('请输入研究股票池')
    return
  }
  researchLoading.value = true
  try {
    researchResult.value = await quantApi.research({ symbols: symbols.slice(0, 50), initial_cash: researchCash.value })
  } finally {
    researchLoading.value = false
  }
}

const signalText = (signal: string) => ({ strong_buy: '强买入', buy: '买入', hold: '观察', avoid: '回避' }[signal] || signal)
const signalTagType = (signal: string) => {
  if (signal === 'strong_buy') return 'success'
  if (signal === 'buy') return 'primary'
  if (signal === 'avoid') return 'danger'
  return 'warning'
}
const factorLabel = (key: string) => ({ trend: '趋势', momentum: '动量', rsi: 'RSI', risk_control: '风控', liquidity: '流动性' }[key] || key)
const formatNumber = (value: number) => new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value || 0)
const formatMoney = (value: number) => {
  const safe = value || 0
  if (Math.abs(safe) >= 100000000) return `${formatNumber(safe / 100000000)}亿`
  if (Math.abs(safe) >= 10000) return `${formatNumber(safe / 10000)}万`
  return formatNumber(safe)
}
const percent = (value: number) => `${((value || 0) * 100).toFixed(2)}%`
const signedPercent = (value?: number | null) => value == null ? '-' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
const changeClass = (value?: number | null) => {
  const safe = Number(value || 0)
  if (safe > 0) return 'text-red'
  if (safe < 0) return 'text-green'
  return ''
}

const chartDrawer = ref(false)
const chartPayload = ref<any>(null)
const chartLoading = ref(false)
const chartTitle = ref('')
const openChart = async (row: any) => {
  chartTitle.value = `${row.name || ''} ${row.code || row.symbol || ''}`
  chartDrawer.value = true
  chartLoading.value = true
  chartPayload.value = null
  try { chartPayload.value = await quantApi.klineDetail(row.code || row.symbol, row.name || '') }
  finally { chartLoading.value = false }
}
</script>

<style scoped lang="scss">
.quant-page {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.text-red {
  color: #f56c6c;
}

.text-green {
  color: #67c23a;
}

.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;

  h1 {
    margin: 0;
    font-size: 22px;
    line-height: 1.3;
  }

  p {
    margin: 4px 0 0;
    color: var(--el-text-color-secondary);
  }
}

.quant-tabs {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 10px 14px 14px;
}

.tool-layout {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 12px;
}

.control-panel,
.result-panel {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px;
  background: var(--el-bg-color);
}

.control-panel {
  align-self: start;

  .el-button + .el-button {
    margin-left: 0;
    margin-top: 10px;
  }
}

.smart-tip {
  margin-bottom: 14px;
}

.smart-home {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.smart-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-extra-light);

  h2 {
    margin: 0;
    font-size: 17px;
    line-height: 1.3;
  }

  p {
    margin: 4px 0 0;
    color: var(--el-text-color-regular);
    line-height: 1.35;
    font-size: 13px;
  }
}

.smart-inline-settings {
  display: flex;
  gap: 10px;
  flex-shrink: 0;

  label {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--el-text-color-secondary);
    font-size: 13px;
  }

  :deep(.el-input-number) {
    width: 118px;
  }
}

.smart-actions {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  flex-shrink: 0;

  span {
    color: var(--el-text-color-secondary);
    font-size: 13px;
  }
}

.smart-settings {
  display: grid;
  grid-template-columns: repeat(2, minmax(220px, 320px));
  gap: 12px;

  > div {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 14px;
    border: 1px solid var(--el-border-color-lighter);
    border-radius: 8px;
    background: var(--el-bg-color);
  }

  span {
    color: var(--el-text-color-secondary);
    white-space: nowrap;
  }
}

.smart-result-panel {
  min-height: 0;
}

.advanced-tools {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 0 14px;
}

.result-panel {
  min-height: 360px;
}

.score-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.score-block {
  display: flex;
  align-items: center;
  gap: 12px;

  strong {
    font-size: 34px;
    line-height: 1;
  }
}

.latest-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(110px, 1fr));
  gap: 10px;
  color: var(--el-text-color-secondary);
  align-items: center;
}

.factor-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 18px;
}

.factor-title {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 18px;

  div {
    border: 1px solid var(--el-border-color-lighter);
    border-radius: 8px;
    padding: 14px;
  }

  span {
    display: block;
    color: var(--el-text-color-secondary);
    margin-bottom: 8px;
  }

  b {
    font-size: 20px;
  }
}

.metric-grid.compact {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 10px;
}

.integration-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 18px;
}

.integration-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 14px;

  h3 {
    margin: 0 0 12px;
    font-size: 16px;
  }

  .el-tag {
    margin: 0 8px 8px 0;
  }
}

.capability-tag {
  margin: 0 6px 6px 0;
}

.mini-summary {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 8px;
  color: var(--el-text-color-secondary);

  b {
    color: var(--el-text-color-primary);
    font-size: 20px;
  }
}

.table-actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.equity-chart {
  height: 260px;
  display: flex;
  align-items: end;
  gap: 2px;
  margin-top: 18px;
  padding: 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  overflow: hidden;
}

.equity-bar {
  flex: 1;
  min-width: 2px;
  background: var(--el-color-primary);
}

@media (max-width: 900px) {
  .tool-layout {
    grid-template-columns: 1fr;
  }

  .score-row,
  .page-head,
  .smart-hero {
    flex-direction: column;
    align-items: flex-start;
  }

  .latest-grid,
  .metric-grid,
  .factor-grid,
  .integration-grid,
  .smart-settings {
    grid-template-columns: 1fr;
  }

  .smart-actions {
    align-items: flex-start;
  }

  .smart-inline-settings {
    flex-wrap: wrap;
  }
}

/* 紧凑表格：一页多显 */
:deep(.el-table__cell) { padding: 4px 0; }
:deep(.el-table .cell) { line-height: 1.3; font-size: 12px; padding-left: 6px; padding-right: 6px; }
:deep(.el-table th.el-table__cell) { padding: 6px 0; }
:deep(.el-tag) { height: 20px; line-height: 18px; padding: 0 5px; font-size: 11px; }
</style>
