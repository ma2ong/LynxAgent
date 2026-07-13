<template>
  <div class="quant-page">
    <section class="page-head">
      <div>
        <h1>智能选股</h1>
        <p>自动横向比较全市场 A 股，优先输出短中期更值得跟踪的量化推荐池</p>
      </div>
      <el-tag effect="plain" type="info">研究跟踪，不构成交易建议</el-tag>
    </section>

    <section v-if="dataHealth" class="data-health" :class="`health-${dataHealth.status}`">
      <div>
        <b>{{ healthTitle }}</b>
        <span>{{ dataHealth.message }}</span>
      </div>
      <div class="health-meta">
        <span>股票池 {{ dataHealth.meta_count }}</span>
        <span>K线 {{ dataHealth.kline_symbols }}</span>
        <span>最新完整 {{ dataHealth.latest_complete_date || '-' }}</span>
        <span>今日 {{ dataHealth.today_count }}/{{ dataHealth.meta_count }}</span>
        <span v-if="dataHealth.gap_dates?.length" class="gap-warn">
          缺口日 {{ dataHealth.gap_dates.join('、') }}（已触发自动补齐，统计自动排除）
        </span>
      </div>
      <div class="health-actions">
        <el-tag v-if="dataHealth.sync_running || syncRunning" type="warning" effect="plain">
          同步中 {{ syncStatus.done || dataHealth.sync_done || 0 }}/{{ syncStatus.total || dataHealth.sync_total || 0 }}
        </el-tag>
        <el-button size="small" :loading="healthLoading || syncRunning" @click="refreshDataHealth(true)">刷新状态</el-button>
        <el-button size="small" type="primary" :loading="syncRunning" :disabled="isIntradayHealth" @click="startSync(false)">收盘后补日线</el-button>
        <el-button size="small" type="danger" plain :loading="syncRunning" @click="startSync(true)">重建历史日线</el-button>
      </div>
    </section>

    <section v-if="marketCtx?.state" class="market-context" :class="`ctx-${ctxTone}`">
      <b>大盘环境：{{ marketCtx.state }}</b>
      <span>近5日{{ marketCtx.as_of ? `(截至 ${marketCtx.as_of})` : '' }}全市场中位 {{ (marketCtx.median_5d_pct ?? 0) > 0 ? '+' : '' }}{{ marketCtx.median_5d_pct }}% · 上涨占比 {{ Math.round((marketCtx.breadth_up || 0) * 100) }}%</span>
      <span class="ctx-advice">{{ marketCtx.advice }}</span>
      <span v-if="coldEvidence" class="ctx-advice ctx-evidence">{{ coldEvidence }}</span>
    </section>

    <el-alert
      v-if="isIntradayHealth"
      title="盘中使用实时行情，日 K 不需要现在补"
      description="智能推荐会使用最近完整日线做结构评分，并叠加实时价格和涨跌幅；市场雷达、涨停热点不依赖今日完整日 K。"
      type="info"
      show-icon
      :closable="false"
      class="smart-tip"
    />

    <el-tabs v-model="activeTab" class="quant-tabs">
      <el-tab-pane label="一键推荐" name="screen">
        <div class="smart-home">
          <section class="smart-hero compact-smart-hero">
            <div>
              <h2>一键智能推荐股票池</h2>
              <p>系统自动综合量化分、趋势、动量、RSI、均线结构、突破信号、成交活跃度、预测因子和风险控制，直接生成当前更值得跟踪的候选股票。</p>
            </div>
            <div class="smart-inline-settings">
              <label class="strategy-pick">
                <span>选股模式</span>
                <el-radio-group v-model="smartPoolForm.strategy" size="default">
                  <el-radio-button label="balanced">全市场动量优选</el-radio-button>
                  <el-radio-button label="swing_short">短线波段(1-3日)</el-radio-button>
                </el-radio-group>
              </label>
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
              <el-button type="success" size="large" native-type="button" :loading="smartPoolLoading" :disabled="patternPoolLoading" @click="loadSmartPool">
                <el-icon><Search /></el-icon>
                一键智能推荐
              </el-button>
              <span v-if="smartPoolResult?.items.length">已推荐 {{ smartPoolResult.items.length }} 只</span>
              <button class="winrate-chip" type="button" @click="router.push('/review')">{{ poolWinText('smart') }}</button>
            </div>
          </section>

          <section class="result-panel smart-result-panel">
            <div v-if="smartPoolLoading" class="smart-progress">
              <div class="progress-head">
                <div>
                  <b>正在生成智能推荐池</b>
                  <span>{{ smartPoolTask?.message || '后台任务已启动，页面会自动刷新结果。' }}</span>
                </div>
                <strong>{{ smartProgress }}%</strong>
              </div>
              <el-progress :percentage="smartProgress" :stroke-width="10" />
              <p style="margin:8px 0 0;font-size:12px;color:#e6a23c;">全市场扫描约需 1~2 分钟，若已有扫描在跑会自动排队，请耐心等待、勿重复点击。</p>
              <div class="progress-steps">
                <div v-for="step in smartProgressSteps" :key="step.name" :class="['progress-step', step.status]">
                  <span>{{ step.index }}</span>
                  <b>{{ step.name }}</b>
                  <em>{{ step.text }}</em>
                </div>
              </div>
            </div>
            <div v-if="smartPoolResult?.items.length" class="mini-summary">
              <span>自动候选 {{ smartPoolResult.universe_size }} 只</span>
              <b>推荐 {{ smartPoolResult.items.length }} 只</b>
              <span v-if="smartPoolResult.analyzed">已分析 {{ smartPoolResult.analyzed }} 只</span>
              <el-tag
                v-if="smartPoolResult.ai_factor"
                size="small"
                :type="smartPoolResult.ai_factor.status === 'ready' ? 'success' : 'warning'"
                effect="plain"
              >
                AI因子 {{ smartPoolResult.ai_factor.status === 'ready' ? `已接入 ${smartPoolResult.ai_factor.pick_date || ''}` : '后台计算中' }}
              </el-tag>
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
                <el-button size="small" type="warning" plain @click="addPoolToPortfolio(smartPoolResult.items, 'smart')">
                  整池加入组合
                </el-button>
              </div>
            </div>
            <el-alert
              v-if="smartPoolResult && smartPoolResult.items.length > 0 && smartPoolResult.items.length < 5"
              type="info" :closable="false" show-icon class="few-picks-tip"
              :title="`当前市场环境下达标标的仅 ${smartPoolResult.items.length} 只`"
              description="评分阈值不随行情放宽（保持口径诚实）。弱市达标少是正常现象，可参考顶部大盘环境提示控制仓位，或等待市场转暖。"
            />
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
              <el-table-column label="AI因子" width="90">
                <template #default="{ row }">
                  <el-tag v-if="row.ai_factor_score" size="small" type="success" effect="plain">
                    {{ Number(row.ai_factor_score).toFixed(0) }}
                  </el-tag>
                  <span v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column label="五方判读" width="110">
                <template #default="{ row }">
                  <el-tooltip v-if="panelScores.smart[row.symbol]" :content="panelScores.smart[row.symbol].summary" placement="top">
                    <el-button text size="small" @click.stop="openPanelDialog(row, 'smart')">
                      <b>{{ panelScores.smart[row.symbol].consensus_score.toFixed(0) }}</b>
                      <span class="panel-div">±{{ panelScores.smart[row.symbol].divergence.toFixed(0) }}</span>
                    </el-button>
                  </el-tooltip>
                  <span v-else class="panel-pending">—</span>
                </template>
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
              <el-table-column label="买卖计划" width="156">
                <template #default="{ row }">
                  <div v-if="row.trade_plan && row.trade_plan.buy_price" class="trade-plan-cell">
                    <span>买 <b>{{ formatNumber(row.trade_plan.buy_price) }}</b></span>
                    <span class="tp-stop">止损 {{ formatNumber(row.trade_plan.stop_loss) }}（{{ row.trade_plan.stop_loss_pct }}%）</span>
                    <span class="tp-target">止盈 {{ formatNumber(row.trade_plan.take_profit) }}（+{{ row.trade_plan.take_profit_pct }}%）</span>
                    <em>盈亏比 {{ row.trade_plan.risk_reward_ratio ?? '-' }}:1 · {{ row.trade_plan.basis === 'atr' ? 'ATR' : '比例' }}</em>
                  </div>
                  <span v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column label="入选理由" min-width="460">
                <template #default="{ row }">
                  <el-tag v-for="reason in row.reasons" :key="reason" class="capability-tag" effect="plain">
                    {{ reason }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="230" fixed="right">
                <template #default="{ row }">
                  <el-button type="primary" link size="small" @click="addOneToFavorites(row)">加入自选</el-button>
                  <el-button link type="primary" size="small" @click="openChart(row)">看图</el-button>
                  <el-button link type="primary" size="small" @click="openWhy(row, 'pattern')">理由</el-button>
                  <el-button link type="warning" size="small" @click="addToPortfolio(row, 'pattern')">+组合</el-button>
                  <el-button link type="primary" size="small" @click="openWhy(row, 'smart')">理由</el-button>
                  <el-button link type="warning" size="small" @click="addToPortfolio(row, 'smart')">+组合</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else-if="!smartPoolLoading" description="点击一键智能推荐后生成量化股票池" />
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
              <el-button type="success" size="large" native-type="button" :loading="patternPoolLoading" :disabled="smartPoolLoading" @click="loadPatternPool">
                <el-icon><Search /></el-icon>
                一键扫描形态
              </el-button>
              <span v-if="patternPoolResult?.items.length">已命中 {{ patternPoolResult.items.length }} 只</span>
              <button class="winrate-chip" type="button" @click="router.push('/review')">{{ poolWinText('pattern') }}</button>
            </div>
          </section>

          <section class="result-panel smart-result-panel">
            <div v-if="patternPoolLoading" class="smart-progress">
              <div class="progress-head">
                <div>
                  <b>正在扫描拉升前形态</b>
                  <span>全市场形态扫描计算量更大，系统会先用最近完整日线，再叠加实时行情兜底。</span>
                </div>
                <strong>{{ smartElapsed }}s</strong>
              </div>
              <el-progress :percentage="100" :indeterminate="true" :duration="3" :stroke-width="10" :show-text="false" />
              <p style="margin:8px 0 0;font-size:12px;color:#e6a23c;">全市场逐只扫描约需 1~2 分钟，系统正在计算，请耐心等待、勿重复点击。</p>
              <div class="progress-steps">
                <div v-for="step in patternProgressSteps" :key="step.name" :class="['progress-step', step.status]">
                  <span>{{ step.index }}</span>
                  <b>{{ step.name }}</b>
                  <em>{{ step.text }}</em>
                </div>
              </div>
            </div>
            <div v-if="patternPoolResult?.items.length" class="mini-summary">
              <span>自动候选 {{ patternPoolResult.universe_size }} 只</span>
              <b>命中 {{ patternPoolResult.matched || patternPoolResult.items.length }} 只</b>
              <span v-if="patternPoolResult.analyzed">已分析 {{ patternPoolResult.analyzed }} 只</span>
              <span v-if="patternPoolResult?.excluded">已排除 {{ patternPoolResult.excluded }} 只</span>
              <span v-if="patternPoolResult?.source === 'live-fallback'" style="color:#e6a23c">本地K线不足，系统已尝试补齐；当前先用实时行情兜底。</span>
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
                <el-button size="small" type="warning" plain @click="addPoolToPortfolio(patternPoolResult.items, 'pattern')">
                  整池加入组合
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
              <el-table-column label="五方判读" width="110">
                <template #default="{ row }">
                  <el-tooltip v-if="panelScores.pattern[row.symbol]" :content="panelScores.pattern[row.symbol].summary" placement="top">
                    <el-button text size="small" @click.stop="openPanelDialog(row, 'pattern')">
                      <b>{{ panelScores.pattern[row.symbol].consensus_score.toFixed(0) }}</b>
                      <span class="panel-div">±{{ panelScores.pattern[row.symbol].divergence.toFixed(0) }}</span>
                    </el-button>
                  </el-tooltip>
                  <span v-else class="panel-pending">—</span>
                </template>
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
              <el-table-column label="买卖计划" width="156">
                <template #default="{ row }">
                  <div v-if="row.trade_plan && row.trade_plan.buy_price" class="trade-plan-cell">
                    <span>买 <b>{{ formatNumber(row.trade_plan.buy_price) }}</b></span>
                    <span class="tp-stop">止损 {{ formatNumber(row.trade_plan.stop_loss) }}（{{ row.trade_plan.stop_loss_pct }}%）</span>
                    <span class="tp-target">止盈 {{ formatNumber(row.trade_plan.take_profit) }}（+{{ row.trade_plan.take_profit_pct }}%）</span>
                    <em>盈亏比 {{ row.trade_plan.risk_reward_ratio ?? '-' }}:1 · {{ row.trade_plan.basis === 'atr' ? 'ATR' : '比例' }}</em>
                  </div>
                  <span v-else>-</span>
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
              <el-table-column label="操作" width="230" fixed="right">
                <template #default="{ row }">
                  <el-button type="primary" link size="small" @click="addOneToFavorites(row)">加入自选</el-button>
                  <el-button link type="primary" size="small" @click="openChart(row)">看图</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else-if="!patternPoolLoading" description="点击一键扫描形态，自动找出符合7类拉升前结构的股票" />
          </section>
        </div>
      </el-tab-pane>

      <el-tab-pane label="数据同步" name="lake">
        <div class="sync-bar" style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px;">
          <el-button type="primary" :loading="syncRunning" @click="startSync(true)">重建历史日线</el-button>
          <el-button :loading="syncRunning" :disabled="isIntradayHealth" @click="startSync(false)">收盘后补日线</el-button>
          <span v-if="syncStatus.total">进度 {{ syncStatus.done }}/{{ syncStatus.total }}（{{ syncStatus.phase }}）失败 {{ syncStatus.errors_count }}</span>
          <el-progress v-if="syncStatus.total" :percentage="syncPct" style="flex:1;min-width:200px;" />
        </div>
        <div class="tool-layout">
          <el-form class="control-panel" label-position="top" @submit.prevent>
            <el-alert
              v-if="dataHealth"
              :title="dataHealth.message"
              :type="dataHealth.ready ? 'success' : 'warning'"
              :closable="false"
              show-icon
              class="smart-tip"
            />
            <el-form-item label="股票池数量">
              <el-input-number v-model="poolLimit" :min="1" :max="5000" style="width: 100%" />
            </el-form-item>
            <el-button native-type="button" :loading="poolLoading" @click="loadPool">读取股票池</el-button>
            <el-button type="primary" native-type="button" :loading="syncRunning" :disabled="isIntradayHealth" @click="startSync(false)">收盘后补日线</el-button>
            <el-button type="danger" plain native-type="button" :loading="syncRunning" @click="startSync(true)">重建历史日线</el-button>
          </el-form>
          <section class="result-panel">
            <div v-if="dataHealth" class="mini-summary">
              <span>状态 {{ healthTitle }}</span>
              <b>{{ dataHealth.kline_symbols }}</b>
              <span>最近完整交易日 {{ dataHealth.latest_complete_date || '-' }}</span>
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
            <el-form-item label="策略（可多选组合）">
              <el-select v-model="backtestForm.strategies" multiple collapse-tags style="width: 100%">
                <el-option label="均线放量" value="ma_volume" />
                <el-option label="海龟突破" value="turtle_breakout" />
                <el-option label="RPS 突破" value="rps_breakout" />
                <el-option label="高窄旗形" value="high_tight_flag" />
                <el-option label="涨停洗盘修复" value="limit_up_washout" />
                <el-option label="多均线共振突破" value="multi_ma_breakout" />
                <el-option label="Keltner 通道突破" value="keltner_breakout" />
              </el-select>
            </el-form-item>
            <el-form-item v-if="backtestForm.strategies.length > 1" label="组合方式">
              <el-select v-model="backtestForm.combine" style="width: 100%">
                <el-option label="全部满足 (AND)" value="and" />
                <el-option label="任一满足 (OR)" value="or" />
                <el-option label="多数满足 (Majority)" value="majority" />
              </el-select>
            </el-form-item>
            <el-form-item label="失效线 (%，0=不启用)">
              <el-input-number v-model="backtestForm.stop_loss_pct" :min="0" :max="50" :step="1" style="width: 100%" />
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
              <div v-if="backtestDiagnosis" class="strategy-diagnosis">
                <div class="diagnosis-head">
                  <div>
                    <span>策略体检</span>
                    <b>{{ backtestDiagnosis.verdict }}</b>
                  </div>
                  <el-progress
                    type="dashboard"
                    :percentage="Math.round(backtestDiagnosis.score)"
                    :width="78"
                    :stroke-width="8"
                  />
                </div>
                <div class="diagnosis-meta">
                  <span>收益回撤比 {{ formatNumber(backtestDiagnosis.rewardRisk) }}</span>
                  <span>引擎 {{ backtestResult.engine }}</span>
                  <span>组合 {{ backtestForm.combine.toUpperCase() }}</span>
                </div>
                <ul>
                  <li v-for="item in backtestDiagnosis.suggestions" :key="item">{{ item }}</li>
                </ul>
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
            <section class="lab-card factor-lab-card">
              <div class="lab-head">
                <div>
                  <h3>AI Factor Lab</h3>
                  <p>把机器学习因子排序、RankIC、TopK 收益和多空组合统一到因子研究页，用来校验一键推荐池里的 AI 因子是否真的有效。</p>
                </div>
                <div class="lab-actions">
                  <el-input-number v-model="mlFactorForm.universe_limit" :min="100" :max="5000" :step="100" controls-position="right" />
                  <el-input-number v-model="mlFactorForm.horizon" :min="1" :max="20" controls-position="right" />
                  <el-input-number v-model="mlFactorForm.k" :min="10" :max="200" :step="10" controls-position="right" />
                  <el-switch v-model="mlFactorForm.neutralize" active-text="中性化" />
                  <el-switch v-model="mlFactorForm.force" active-text="重算" />
                  <el-button type="success" :loading="mlFactorLoading" @click="runMLFactorLab">运行AI因子实验</el-button>
                </div>
              </div>

              <el-alert
                v-if="mlFactorResult?.status === 'computing'"
                title="AI 因子模型正在后台计算，稍后重新运行会读取最新缓存。"
                type="warning"
                :closable="false"
                show-icon
              />
              <el-alert
                v-else-if="mlFactorResult?.status === 'error'"
                :title="mlFactorResult.error || 'AI 因子实验失败'"
                type="error"
                :closable="false"
                show-icon
              />

              <template v-if="mlFactorResult?.status === 'ready'">
                <div class="mini-summary">
                  <span>样本 {{ mlFactorResult.universe }} 只</span>
                  <span>周期 {{ mlFactorResult.horizon }} 日</span>
                  <span>TopK {{ mlFactorResult.k }}</span>
                  <b>{{ mlFactorResult.pick_date }}</b>
                  <el-tag size="small" :type="mlFactorResult.cached ? 'info' : 'success'" effect="plain">
                    {{ mlFactorResult.cached ? '缓存结果' : '新计算' }}
                  </el-tag>
                </div>
                <div class="metric-grid">
                  <div><span>RankIC</span><b>{{ formatNumber(mlFactorResult.ic?.rank_ic_mean || 0) }}</b></div>
                  <div><span>ICIR</span><b>{{ formatNumber(mlFactorResult.ic?.rank_icir || 0) }}</b></div>
                  <div><span>TopK年化</span><b>{{ percent(mlFactorResult.metrics?.topk?.annual_return || 0) }}</b></div>
                  <div><span>TopK回撤</span><b>{{ percent(mlFactorResult.metrics?.topk?.max_drawdown || 0) }}</b></div>
                  <div><span>TopK胜率</span><b>{{ percent(mlFactorResult.metrics?.topk?.win_rate || 0) }}</b></div>
                  <div><span>多空年化</span><b>{{ percent(mlFactorResult.metrics?.long_short?.annual_return || 0) }}</b></div>
                </div>
                <div class="factor-lab-grid">
                  <div>
                    <h4>Top Features</h4>
                    <div v-for="item in topMLFeatures" :key="item.name" class="feature-row">
                      <span>{{ item.name }}</span>
                      <b>{{ formatNumber(item.value) }}</b>
                    </div>
                  </div>
                  <div>
                    <h4>Latest Picks</h4>
                    <div v-for="pick in mlFactorResult.picks.slice(0, 10)" :key="pick.symbol" class="feature-row">
                      <span>{{ pick.symbol }} {{ pick.name }}</span>
                      <b>{{ formatNumber(pick.score) }}</b>
                    </div>
                  </div>
                </div>
              </template>
            </section>

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

    <el-dialog v-model="panelDialogVisible" :title="panelDialogTitle" width="560px">
      <template v-if="panelDialogData">
        <p class="panel-summary">{{ panelDialogData.summary }}</p>
        <el-table :data="panelDialogData.verdicts" size="small">
          <el-table-column prop="persona" label="评委" width="90" />
          <el-table-column prop="score" label="评分" width="70" />
          <el-table-column label="立场" width="80">
            <template #default="{ row }">
              <el-tag size="small" :type="stanceType(row.stance)">{{ row.stance }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reason" label="一句话理由" min-width="220" />
        </el-table>
        <p class="panel-note">AI 模拟多风格视角生成，仅供参考，非投资建议。</p>
      </template>
    </el-dialog>

    <WhyPickedDrawer v-model="whyVisible" :row="whyRow" :pool="whyPool" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { DataLine, Search, TrendCharts } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'
import { favoritesApi } from '@/api/favorites'
import KLineProChart from '@/components/KLineProChart.vue'
import WhyPickedDrawer from '@/components/WhyPickedDrawer.vue'
import {
  quantApi,
  type BacktestResult,
  type FactorResearchResult,
  type ForecastResult,
  type MLFactorResult,
  type PatternRecognitionResult,
  type QuantDataHealth,
  type QuantAnalysisResult,
  type QuantPatternPoolItem,
  type QuantPatternPoolResult,
  type QuantScreenResult,
  type QuantSmartPoolItem,
  type QuantSmartPoolResult,
  type QuantSmartPoolTask,
  type QuantStockPoolResult,
  type MarketContext
} from '@/api/quant'
import { panelApi, portfolioApi, type PanelScore } from '@/api/quant'

const route = useRoute()
const routeInitialTab = () => String(route.meta.initialTab || route.query.tab || 'screen')
const activeTab = ref(routeInitialTab())

const router = useRouter()

watch(
  () => route.fullPath,
  () => {
    activeTab.value = routeInitialTab()
  },
)

const poolLimit = ref(200)
const poolLoading = ref(false)
const poolResult = ref<QuantStockPoolResult | null>(null)
const dataHealth = ref<QuantDataHealth | null>(null)

const whyVisible = ref(false)
const whyRow = ref<any | null>(null)
const whyPool = ref<'smart' | 'pattern'>('smart')
const openWhy = (row: any, pool: 'smart' | 'pattern') => {
  whyRow.value = row
  whyPool.value = pool
  whyVisible.value = true
}

const addToPortfolio = async (row: any, source: 'smart' | 'pattern') => {
  try {
    const pos = await portfolioApi.add({ symbol: row.symbol, name: row.name, source })
    ElMessage.success(`已加入模拟组合：${row.name} ${pos?.shares || ''}股，可到「模拟组合」页跟踪`)
  } catch (e: any) {
    ElMessage.warning(e?.message || '加入组合失败')
  }
}

// 整池等权加入：回放数据表明超额只在组合层面成立（均值靠右尾、单票中位为负），
// 跟池必须整池买、不能只挑一两只——把这个结论变成一键动作。
const POOL_BUDGET = 10000
const addPoolToPortfolio = async (items: any[], source: 'smart' | 'pattern') => {
  const rows = (items || []).filter((r) => r?.symbol)
  if (!rows.length) return
  try {
    await ElMessageBox.confirm(
      `将把当前 ${rows.length} 只候选按每票 ¥${POOL_BUDGET.toLocaleString()} 预算整手买入模拟组合` +
      `（预计动用约 ¥${(rows.length * POOL_BUDGET).toLocaleString()}，含 A 股交易成本）。` +
      '历史回放显示该池收益依赖整池分散，单票胜率约五成——建议整池跟踪而非单票押注。确认加入？',
      '整池加入模拟组合',
      { confirmButtonText: '整池买入', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  try {
    const res = await portfolioApi.addBatch({
      items: rows.map((r) => ({ symbol: r.symbol, name: r.name, price: Number(r.close) || undefined })),
      budget_per_stock: POOL_BUDGET,
      source,
    })
    if (!res) throw new Error('无返回')
    const skippedReasons = res.results.filter((r) => !r.ok).slice(0, 3)
      .map((r) => `${r.name}(${r.reason})`).join('、')
    if (res.skipped > 0) {
      ElMessage.warning(`已买入 ${res.added} 只，跳过 ${res.skipped} 只：${skippedReasons}${res.skipped > 3 ? ' 等' : ''}`)
    } else {
      ElMessage.success(`已整池买入 ${res.added} 只，可到「模拟组合」页跟踪盈亏与卖出信号`)
    }
  } catch (e: any) {
    ElMessage.warning(e?.message || '整池加入失败')
  }
}
const healthLoading = ref(false)

const analysisForm = ref({ symbol: '600519' })
const analysisRange = ref<[string, string] | null>(null)
const analysisLoading = ref(false)
const analysisResult = ref<QuantAnalysisResult | null>(null)

const screenSymbolsText = ref('600519\n000001\n300750')
const screenForm = ref({ limit: 30 })
const screenLoading = ref(false)
const screenResult = ref<QuantScreenResult | null>(null)
const smartPoolForm = ref({ limit: 20, universe_limit: 5000, strategy: 'balanced' })
const smartPoolLoading = ref(false)
const smartPoolResult = ref<QuantSmartPoolResult | null>(null)
const smartPoolTask = ref<QuantSmartPoolTask | null>(null)
const smartTableRef = ref<any>()
const selectedSmartRows = ref<QuantSmartPoolItem[]>([])
const patternPoolForm = ref({ limit: 20, universe_limit: 5000, min_strength: 70, exclude_fundamental: true })
const patternPoolLoading = ref(false)
const patternPoolResult = ref<QuantPatternPoolResult | null>(null)
const patternTableRef = ref<any>()
const selectedPatternRows = ref<QuantPatternPoolItem[]>([])
const smartElapsed = ref(0)
let smartProgressTimer: number | undefined
let smartTaskTimer: number | undefined

const backtestForm = ref({
  symbol: '600519',
  strategies: ['ma_volume'] as string[],
  combine: 'and',
  stop_loss_pct: 0,
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
const mlFactorForm = ref({
  universe_limit: 1000,
  horizon: 5,
  k: 50,
  mode: 'rolling' as 'rolling' | 'once',
  neutralize: true,
  retrain_every: 20,
  force: false
})
const mlFactorLoading = ref(false)
const mlFactorResult = ref<MLFactorResult | null>(null)

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

const topMLFeatures = computed(() =>
  Object.entries(mlFactorResult.value?.top_features || {})
    .map(([name, value]) => ({ name, value: Number(value || 0) }))
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    .slice(0, 10)
)

const backtestDiagnosis = computed(() => {
  const r = backtestResult.value
  if (!r) return null
  const totalReturn = Number(r.total_return || 0)
  const annualReturn = Number(r.annualized_return || 0)
  const maxDrawdown = Math.abs(Number(r.max_drawdown || 0))
  const winRate = Number(r.win_rate || 0)
  const trades = Number(r.trades || 0)
  const rewardRisk = maxDrawdown > 0 ? totalReturn / maxDrawdown : (totalReturn > 0 ? 99 : 0)
  let score = 50
  score += Math.min(25, annualReturn * 100)
  score += Math.min(15, winRate * 20)
  score -= Math.min(25, maxDrawdown * 100)
  if (trades < 5) score -= 10
  if (rewardRisk > 1.5) score += 10
  if (rewardRisk < 0.5) score -= 10
  score = Math.max(0, Math.min(100, score))
  const verdict = score >= 70 ? '可继续研究' : score >= 50 ? '参数需优化' : '暂停使用'
  const suggestions: string[] = []
  if (trades < 5) suggestions.push('交易次数偏少，样本不足，先拉长回测区间或换更活跃标的。')
  if (maxDrawdown > 0.25) suggestions.push('最大回撤超过 25%，需要收紧失效线、降低风险暴露或增加趋势过滤。')
  if (annualReturn <= 0) suggestions.push('年化收益为负，当前参数不具备继续横向验证价值。')
  if (winRate < 0.45) suggestions.push('胜率偏低，检查触发条件是否太宽，优先减少假突破。')
  if (rewardRisk < 1) suggestions.push('收益回撤比不足 1，策略承担的波动没有换来足够回报。')
  if (!suggestions.length) suggestions.push('收益、回撤和胜率暂时匹配，可进入更多股票和更长周期的横向验证。')
  return { score, verdict, rewardRisk, suggestions }
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
const isIntradayHealth = computed(() => dataHealth.value?.status === 'intraday')
const healthTitle = computed(() => {
  const status = dataHealth.value?.status
  if (status === 'fresh') return '今日已更新'
  if (status === 'intraday') return '盘中实时行情'
  if (status === 'partial_today') return '今日补齐中'
  if (status === 'stale_today') return '等待今日数据'
  if (status === 'ready') return '本地数据可用'
  if (status === 'insufficient') return '本地数据不足'
  if (status === 'empty') return '本地数据为空'
  return '数据状态'
})
const smartProgress = computed(() => {
  const value = smartPoolTask.value?.progress
  if (typeof value === 'number') return Math.max(1, Math.min(100, Math.round(value)))
  return Math.min(92, 8 + smartElapsed.value * 3)
})
const smartStepStatus = (activeAt: number, doneAt: number) => {
  const progress = smartProgress.value
  if (progress >= doneAt) return 'done'
  if (progress >= activeAt) return 'active'
  return 'pending'
}
const smartProgressSteps = computed(() => {
  return [
    {
      index: '1',
      name: '检查数据',
      text: dataHealth.value?.latest_complete_date ? `最近完整日线 ${dataHealth.value.latest_complete_date}` : '确认本地行情池可用',
      status: smartStepStatus(1, 14)
    },
    {
      index: '2',
      name: '抽取行情',
      text: isIntradayHealth.value ? '叠加盘中实时价格和涨跌幅' : '读取本地 K 线与最新行情',
      status: smartStepStatus(14, 52)
    },
    {
      index: '3',
      name: '量化评分',
      text: `横向比较 ${smartPoolForm.value.universe_limit} 只候选股票`,
      status: smartStepStatus(52, 86)
    },
    {
      index: '4',
      name: '输出候选池',
      text: `筛出达标候选（上限 ${smartPoolForm.value.limit} 只，弱市达标少属正常）`,
      status: smartStepStatus(86, 100)
    }
  ]
})
const patternProgressSteps = computed(() => {
  const elapsed = smartElapsed.value
  return [
    {
      index: '1',
      name: '检查日线',
      text: dataHealth.value?.latest_complete_date ? `最近完整日线 ${dataHealth.value.latest_complete_date}` : '确认 K 线样本可用',
      status: elapsed >= 2 ? 'done' : 'active'
    },
    {
      index: '2',
      name: '结构扫描',
      text: `扫描 ${patternPoolForm.value.universe_limit} 只候选的均线、量能和突破结构`,
      status: elapsed >= 12 ? 'done' : elapsed >= 2 ? 'active' : 'pending'
    },
    {
      index: '3',
      name: '过滤风险',
      text: '剔除基本面或形态证据不足的标的',
      status: elapsed >= 24 ? 'done' : elapsed >= 12 ? 'active' : 'pending'
    },
    {
      index: '4',
      name: '输出结果',
      text: `返回前 ${patternPoolForm.value.limit} 只形态更清晰的股票`,
      status: elapsed >= 24 ? 'active' : 'pending'
    }
  ]
})

const startSmartProgress = () => {
  smartElapsed.value = 0
  if (smartProgressTimer) window.clearInterval(smartProgressTimer)
  smartProgressTimer = window.setInterval(() => {
    smartElapsed.value += 1
  }, 1000)
}

const stopSmartProgress = () => {
  if (smartProgressTimer) window.clearInterval(smartProgressTimer)
  smartProgressTimer = undefined
}
const stopSmartTaskPolling = () => {
  if (smartTaskTimer) window.clearTimeout(smartTaskTimer)
  smartTaskTimer = undefined
}
let syncTimer: number | undefined
let syncWatching = false
const pollSync = async () => {
  syncStatus.value = await quantApi.syncStatus()
  if (syncStatus.value.health) dataHealth.value = syncStatus.value.health
  if (syncStatus.value.running) {
    syncTimer = window.setTimeout(pollSync, 2500)
  } else if (syncWatching) {
    syncWatching = false
    const s = syncStatus.value
    await refreshDataHealth(false)
    smartPoolResult.value = null
    patternPoolResult.value = null
    ElMessage.success(`同步完成：${s.done}/${s.total} 只，失败 ${s.errors_count || 0}。已刷新本地数据状态。`)
  }
}
const startSync = async (full: boolean) => {
  if (!full && isIntradayHealth.value) {
    ElMessage.info('当前是盘中实时行情模式，收盘后再补日线；智能选股会先使用最近完整日线。')
    return
  }
  try {
    const s = await quantApi.syncMarket(full)
    if (s && typeof s === 'object') syncStatus.value = s
    syncWatching = true
    ElMessage.success(full
      ? '已开始重建历史日线（约 5000 只，后台进行，可切换页面，完成约需几分钟）'
      : '已开始收盘后日线补齐')
    pollSync()
  } catch (error: any) {
    ElMessage.error(error?.message || '启动同步失败')
  }
}
const refreshDataHealth = async (autoStart = true) => {
  healthLoading.value = true
  try {
    const health = await quantApi.dataHealth(autoStart)
    dataHealth.value = health
    if (health.sync_running || health.auto_started) {
      syncWatching = true
      pollSync()
    }
    return health
  } catch (error: any) {
    ElMessage.error(error?.message || '检查本地数据状态失败')
    return null
  } finally {
    healthLoading.value = false
  }
}

const ensureDataBeforeScan = async () => {
  const health = await refreshDataHealth(true)
  if (!health) return false
  if (!health.ready) {
    activeTab.value = 'lake'
    ElMessage.warning('本地K线不足，已切到数据同步；请先完成一次历史日线重建。')
    return false
  }
  if (health.needs_incremental_sync || health.sync_running || health.auto_started) {
    ElMessage.info('今日数据正在自动补齐；本次先使用最近完整交易日数据，完成后会自动刷新。')
  }
  return true
}

const marketCtx = ref<MarketContext | null>(null)
const ctxTone = computed(() => {
  const s = marketCtx.value?.state
  return s === '偏暖' ? 'warm' : s === '偏冷' ? 'cold' : 'flat'
})

// 偏冷时给回放证据：历史上偏冷期两池超额如何（接口有缓存，<0.1s）
const coldEvidence = ref('')
const loadColdEvidence = async () => {
  if (marketCtx.value?.state !== '偏冷') return
  try {
    const rep = await quantApi.replayResults()
    const parts: string[] = []
    for (const p of rep?.pools || []) {
      const cold = (p.regimes || []).find((r) => r.regime === '偏冷')
      if (cold) {
        const label = p.pool === 'pattern' ? '形态池' : p.pool === 'smart' ? '智能池' : p.pool
        parts.push(`${label} ${cold.avg_excess > 0 ? '+' : ''}${cold.avg_excess}pp（中位 ${cold.median_excess}pp，${cold.picks} 样本）`)
      }
    }
    if (parts.length) coldEvidence.value = `历史回放偏冷期 T+5 超额：${parts.join('；')}——建议轻仓或只跟不买。`
  } catch { /* 无回放结果时不展示 */ }
}

// 各池近30日 T+5 真实胜率（来自选股留痕），样本不足时提示积累中
const poolStats = ref<Record<string, { win_rate: number | null; samples: number }>>({})
const poolWinText = (pool: string) => {
  const s = poolStats.value[pool]
  if (!s || !s.samples || s.win_rate == null) return '真实胜率统计积累中 · 查看复盘'
  return `近30日 T+5 胜率 ${(s.win_rate * 100).toFixed(0)}%（${s.samples} 样本）`
}

onMounted(async () => {
  quantApi.marketContext().then((ctx) => { marketCtx.value = ctx || null; loadColdEvidence() }).catch(() => {})
  quantApi.picksStats(30).then((res) => {
    const map: Record<string, { win_rate: number | null; samples: number }> = {}
    for (const p of res?.pools || []) {
      const t5 = p.horizons?.t5
      map[p.pool] = { win_rate: t5?.win_rate ?? null, samples: t5?.samples ?? 0 }
    }
    poolStats.value = map
  }).catch(() => {})
  try {
    const s = await quantApi.syncStatus()
    syncStatus.value = s
    if (s.health) dataHealth.value = s.health
    if (s.running) pollSync()
  } finally {
    refreshDataHealth(true)
  }
})
onUnmounted(() => {
  if (syncTimer) window.clearTimeout(syncTimer)
  stopSmartTaskPolling()
  stopSmartProgress()
  Object.values(panelTimers).forEach(t => window.clearTimeout(t))
})

// 五方判读批量评分：pool -> symbol -> score；后台补打时轮询
const panelScores = ref<Record<string, Record<string, PanelScore>>>({ smart: {}, pattern: {} })
const panelDialogVisible = ref(false)
const panelDialogData = ref<PanelScore | null>(null)
const panelDialogTitle = ref('')
const panelTimers: Record<string, number> = {}

const loadPanelScores = async (pool: 'smart' | 'pattern', attempt = 0) => {
  try {
    const data = await panelApi.batch(pool)
    if (!data) return
    panelScores.value[pool] = data.items || {}
    if (data.pending > 0 && attempt < 10) {
      if (panelTimers[pool]) window.clearTimeout(panelTimers[pool])
      panelTimers[pool] = window.setTimeout(() => loadPanelScores(pool, attempt + 1), 20000)
    }
  } catch { /* 判读是增强信息，失败静默 */ }
}

const openPanelDialog = (row: { symbol: string; name?: string }, pool: 'smart' | 'pattern') => {
  const score = panelScores.value[pool]?.[row.symbol]
  if (!score) return
  panelDialogData.value = score
  panelDialogTitle.value = `${row.name || row.symbol} · 五方判读`
  panelDialogVisible.value = true
}

const stanceType = (stance: string) => (stance === '看多' ? 'danger' : stance === '看空' ? 'success' : 'info')

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

const finishSmartPoolTask = () => {
  stopSmartTaskPolling()
  stopSmartProgress()
  smartPoolLoading.value = false
}

const pollSmartPoolTask = async (taskId: string) => {
  try {
    const task = await quantApi.smartPoolTask(taskId)
    smartPoolTask.value = task
    if (task.status === 'completed') {
      if (!task.result) throw new Error('智能推荐任务已完成，但结果为空')
      smartPoolResult.value = task.result
      selectedSmartRows.value = []
      loadPanelScores('smart')
      finishSmartPoolTask()
      ElMessage.success(`智能推荐完成：${task.result.items.length} 只`)
      return
    }
    if (task.status === 'failed') {
      throw new Error(task.error || task.message || '智能推荐任务失败')
    }
    if (smartElapsed.value > 300) {
      finishSmartPoolTask()
      smartPoolResult.value = null
      ElMessage.warning('智能推荐超时（后台任务 5 分钟未完成），请刷新或稍后重试')
      return
    }
    smartTaskTimer = window.setTimeout(() => pollSmartPoolTask(taskId), 1500)
  } catch (error: any) {
    finishSmartPoolTask()
    smartPoolResult.value = null
    ElMessage.error(error?.message || '智能推荐失败，请重试')
  }
}

const loadSmartPool = async () => {
  stopSmartTaskPolling()
  smartPoolLoading.value = true
  smartPoolTask.value = null
  smartPoolResult.value = null
  startSmartProgress()
  try {
    if (!(await ensureDataBeforeScan())) {
      finishSmartPoolTask()
      return
    }
    const task = await quantApi.startSmartPoolTask(smartPoolForm.value.limit, smartPoolForm.value.universe_limit, smartPoolForm.value.strategy)
    smartPoolTask.value = task
    screenResult.value = null
    selectedSmartRows.value = []
    ElMessage.success('智能推荐已转入后台任务，完成后自动展示结果')
    pollSmartPoolTask(task.task_id)
  } catch (error: any) {
    finishSmartPoolTask()
    smartPoolResult.value = null
    ElMessage.error(error?.message || '启动智能推荐失败')
  }
}

const handleSmartSelectionChange = (rows: QuantSmartPoolItem[]) => {
  selectedSmartRows.value = rows
}

const formatScore = (value?: number | null) => Number(value ?? 0).toFixed(1)

const displayScore = (row: QuantSmartPoolItem) => formatScore(row.quant_score ?? row.score)

const loadPatternPool = async () => {
    patternPoolLoading.value = true
    smartPoolTask.value = null
    startSmartProgress()
    try {
        if (!(await ensureDataBeforeScan())) return
        patternPoolResult.value = await quantApi.patternPool(
            patternPoolForm.value.limit,
            patternPoolForm.value.universe_limit,
            patternPoolForm.value.min_strength
        )
        selectedPatternRows.value = []
        loadPanelScores('pattern')
    } catch (error: any) {
        ElMessage.error(error?.message || '形态扫描失败，请重试')
        patternPoolResult.value = null
    } finally {
        stopSmartProgress()
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
  if (!backtestForm.value.strategies.length) {
    ElMessage.warning('请至少选择一个策略')
    return
  }
  backtestLoading.value = true
  try {
    backtestResult.value = await quantApi.backtest({
      symbol: backtestForm.value.symbol.trim(),
      strategies: backtestForm.value.strategies,
      combine: backtestForm.value.combine,
      stop_loss_pct: (backtestForm.value.stop_loss_pct || 0) / 100,
      engine: backtestForm.value.engine,
      initial_cash: backtestForm.value.initial_cash,
      ...parseRange(backtestRange.value)
    })
  } catch (error: any) {
    ElMessage.error(error?.message || '回测失败')
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

const runMLFactorLab = async () => {
  mlFactorLoading.value = true
  try {
    mlFactorResult.value = await quantApi.factorModel({ ...mlFactorForm.value })
    if (mlFactorResult.value?.status === 'ready') {
      ElMessage.success('AI 因子实验完成')
    } else if (mlFactorResult.value?.status === 'computing') {
      ElMessage.warning('AI 因子模型正在后台计算，稍后再刷新结果')
    } else if (mlFactorResult.value?.error) {
      ElMessage.error(mlFactorResult.value.error)
    }
  } catch (error: any) {
    ElMessage.error(error?.message || 'AI 因子实验失败')
  } finally {
    mlFactorLoading.value = false
  }
}

const signalText = (signal: string) => ({ strong_buy: '强势跟踪', buy: '重点跟踪', hold: '观察', avoid: '回避' }[signal] || signal)
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

.data-health {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) auto auto;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-fill-color-extra-light);

  b {
    display: block;
    margin-bottom: 2px;
  }

  span {
    color: var(--el-text-color-secondary);
    font-size: 12px;
  }
}

.winrate-chip {
  border: 1px solid var(--el-border-color-light);
  border-radius: 999px;
  background: var(--el-fill-color-extra-light);
  color: var(--el-text-color-secondary);
  font-size: 12px;
  padding: 4px 10px;
  cursor: pointer;

  &:hover {
    color: var(--el-color-primary);
    border-color: var(--el-color-primary-light-5);
  }
}

.market-context {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-fill-color-extra-light);

  b { font-size: 14px; }
  span { color: var(--el-text-color-secondary); font-size: 12px; }
  .ctx-advice { color: var(--el-text-color-regular); }
  .ctx-evidence { font-size: 12px; opacity: 0.85; }
}

.ctx-warm {
  border-color: #ffb3a7;
  background: #fff1f0;
  b { color: #ef232a; }
}

.ctx-cold {
  border-color: #a7d4b4;
  background: #f0fff4;
  b { color: #0e9f5a; }
}

.health-fresh,
.health-ready {
  border-color: #b7eb8f;
  background: #f6ffed;
}

.health-partial_today,
.health-stale_today {
  border-color: #ffe58f;
  background: #fffbe6;
}

.health-empty,
.health-insufficient {
  border-color: #ffccc7;
  background: #fff2f0;
}

.health-meta,
.health-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.gap-warn {
  color: #cf1322;
  font-weight: 600;
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
  flex-wrap: wrap;
  align-items: center;

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

.smart-progress {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border: 1px solid #d9ecff;
  border-radius: 8px;
  background: #f5faff;
}

.progress-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;

  b {
    display: block;
    margin-bottom: 4px;
    font-size: 15px;
  }

  span {
    color: var(--el-text-color-secondary);
    font-size: 13px;
  }

  strong {
    color: var(--el-color-primary);
    font-size: 18px;
    white-space: nowrap;
  }
}

.progress-steps {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.progress-step {
  min-height: 78px;
  padding: 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);

  span {
    display: inline-flex;
    width: 22px;
    height: 22px;
    align-items: center;
    justify-content: center;
    margin-bottom: 8px;
    border-radius: 50%;
    background: var(--el-fill-color-light);
    color: var(--el-text-color-secondary);
    font-size: 12px;
    font-weight: 700;
  }

  b {
    display: block;
    margin-bottom: 4px;
    font-size: 13px;
  }

  em {
    display: block;
    color: var(--el-text-color-secondary);
    font-size: 12px;
    font-style: normal;
    line-height: 1.45;
  }
}

.progress-step.active {
  border-color: #91caff;

  span {
    background: var(--el-color-primary);
    color: #fff;
  }
}

.progress-step.done {
  border-color: #b7eb8f;

  span {
    background: #67c23a;
    color: #fff;
  }
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

.trade-plan-cell {
  display: flex;
  flex-direction: column;
  gap: 1px;
  font-size: 12px;
  line-height: 1.5;

  b { font-weight: 700; }
  .tp-stop { color: var(--el-color-success); }
  .tp-target { color: var(--el-color-danger); }
  em {
    font-style: normal;
    color: var(--el-text-color-secondary);
    font-size: 11px;
  }
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

.few-picks-tip {
  margin-bottom: 8px;
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

.lab-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
  background: var(--el-fill-color-extra-light);
}

.lab-head {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: flex-start;
  margin-bottom: 12px;

  h3 {
    margin: 0 0 4px;
    font-size: 16px;
  }

  p {
    margin: 0;
    color: var(--el-text-color-secondary);
    font-size: 13px;
    line-height: 1.5;
  }
}

.lab-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
  min-width: 480px;

  :deep(.el-input-number) {
    width: 112px;
  }
}

.factor-lab-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 12px;

  h4 {
    margin: 0 0 8px;
    font-size: 13px;
    color: var(--el-text-color-secondary);
  }
}

.feature-row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
  font-size: 13px;

  span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.strategy-diagnosis {
  margin-top: 14px;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-extra-light);

  ul {
    margin: 10px 0 0;
    padding-left: 18px;
    color: var(--el-text-color-regular);
    line-height: 1.8;
    font-size: 13px;
  }
}

.diagnosis-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;

  span {
    display: block;
    color: var(--el-text-color-secondary);
    font-size: 13px;
    margin-bottom: 4px;
  }

  b {
    font-size: 22px;
  }
}

.diagnosis-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 8px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
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

  .progress-steps {
    grid-template-columns: 1fr;
  }

  .lab-head {
    flex-direction: column;
  }

  .lab-actions {
    justify-content: flex-start;
    min-width: 0;
  }

  .factor-lab-grid {
    grid-template-columns: 1fr;
  }
}

/* 紧凑表格：一页多显 */
:deep(.el-table__cell) { padding: 4px 0; }
:deep(.el-table .cell) { line-height: 1.3; font-size: 12px; padding-left: 6px; padding-right: 6px; }
:deep(.el-table th.el-table__cell) { padding: 6px 0; }
:deep(.el-tag) { height: 20px; line-height: 18px; padding: 0 5px; font-size: 11px; }

.panel-div { margin-left: 2px; font-size: 11px; color: var(--el-text-color-secondary); }
.panel-pending { color: var(--el-text-color-placeholder); }
.panel-summary { margin: 0 0 10px; font-size: 13px; }
.panel-note { margin: 10px 0 0; font-size: 12px; color: var(--el-text-color-placeholder); }
</style>
