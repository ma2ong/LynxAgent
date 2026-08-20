<template>
  <div
    class="quant-page"
    :style="{ '--tabs-head-h': tabsHeadHeight + 'px', '--sticky-head-h': stickyHeadHeight + 'px' }"
  >
    <el-alert
      v-if="riskLocked && riskAlert"
      type="error"
      show-icon
      :closable="false"
      class="risk-lock-alert"
    >
      <template #title>
        <div class="risk-lock-body">
          <b>风险「{{ riskAlert.level }}」·推荐已转为观察名单</b>
          <span class="risk-action">{{ riskAlert.action }}</span>
          <el-button size="small" type="danger" plain @click="router.push('/risk-alert')">风险明细</el-button>
        </div>
      </template>
    </el-alert>

    <section class="page-head">
      <div class="head-line">
        <h1>智能选股</h1>
        <p>全市场量价动态优选，优先展示当前最优名单</p>
      </div>
      <div class="head-tags">
        <el-tag
          v-if="isIntradayHealth"
          effect="plain"
          type="success"
          title="交易时段内每30秒按实时价格、涨跌幅、成交活跃度、量比和雷达时机自动重排"
        >盘中30秒更新</el-tag>
        <!-- 数据状态并进标题行：它平时只有一句「今日已更新」，独占一整栏太浪费版面，
             展开的明细仍然在下面那块，只是不展开就不占位。 -->
        <el-tag
          v-if="dataHealth"
          class="health-chip"
          effect="plain"
          :type="healthChipType"
          :title="dataHealth.message"
        >{{ healthTitle }} {{ dataHealth.today_count }}/{{ dataHealth.meta_count }}</el-tag>
        <a v-if="dataHealth" class="desc-toggle" @click="showHealthDetail = !showHealthDetail">
          {{ showHealthDetail ? '收起' : '明细' }}
        </a>
        <el-tag effect="plain" type="info">研究跟踪，不构成交易建议</el-tag>
      </div>
    </section>

    <section v-if="dataHealth && showHealthDetail" class="data-health" :class="`health-${dataHealth.status}`">
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

      <div class="smart-home">
        <section class="smart-hero compact-smart-hero">
          <div>
            <h2>
              一键智能推荐股票池
              <!-- 这段说明四行、只在第一次看时有用，之后天天占掉表格的空间，故默认收起 -->
              <a class="desc-toggle" @click="showPoolDesc = !showPoolDesc">
                {{ showPoolDesc ? '收起说明' : '选股逻辑' }}
              </a>
            </h2>
            <p v-if="showPoolDesc">结构因子先从全市场筛出强结构备选池（MACD、布林位置、趋势、动量、资金流，与历史回放同源），盘中爆发只允许结构质量前 10% 的股票晋级，再按实时量价与雷达时机重排；涨停或距离涨停过近会醒目标注买入难度但照常上榜；最终只保留综合排序 ≥ 90 分的标的，够分几只就给几只，不再按名次砍到 20 只。只有绿色“量价已确认”才代表通过了第二重条件。</p>
          </div>
          <div class="smart-inline-settings">
            <label>
              <span>候选</span>
              <el-input-number v-model="smartPoolForm.universe_limit" :min="50" :max="10000" :step="50" controls-position="right" />
            </label>
            <span class="score-floor-chip" title="综合排序分＝日K结构分与盘中量价融合后的 0~100 刻度。达到门槛就上榜，多少只都给；今天没有够分的就不给。">
              入选门槛 综合排序 ≥ <b>{{ smartScoreFloor }}</b> 分 · 不限只数
            </span>
          </div>
          <div class="smart-actions">
            <el-button type="success" native-type="button" :loading="smartPoolLoading" @click="loadSmartPool">
              <el-icon><Search /></el-icon>
              {{ riskLocked ? '生成强势观察名单' : '一键智能推荐' }}
            </el-button>
            <span v-if="smartPoolResult?.items.length">
              {{ riskLocked ? '观察' : '已推荐' }} {{ smartPoolResult.items.length }} 只
            </span>
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
            <el-progress
              :percentage="smartProgress"
              :stroke-width="10"
              :indeterminate="smartPoolTask?.phase === 'quant_center'"
              :duration="4"
            />
            <p style="margin:8px 0 0;font-size:12px;color:#e6a23c;">
              {{ smartPoolTask?.phase === 'quant_center'
                ? `当天首次全市场评分约需 1~2 分钟，已运行 ${smartElapsed} 秒；完成后当天再次推荐将优先秒读缓存。`
                : '优先读取当天强结构备选池，再按最新价格、成交量能和盘中信号动态重排。' }}
            </p>
            <div class="progress-steps">
              <div v-for="step in smartProgressSteps" :key="step.name" :class="['progress-step', step.status]">
                <span>{{ step.index }}</span>
                <b>{{ step.name }}</b>
                <em>{{ step.text }}</em>
              </div>
            </div>
          </div>
          <div v-if="smartPoolResult?.items.length" class="mini-summary smart-summary">
            <div class="summary-primary">
              <b>{{ riskLocked ? '观察名单' : '推荐' }} {{ smartPoolResult.items.length }} 只</b>
              <el-tag
                v-if="smartPoolResult.dual_confirm_count"
                size="small" :type="smartDualOnly ? 'success' : 'info'" effect="plain"
                class="dc-filter" @click="smartDualOnly = !smartDualOnly"
                :title="'结构因子 + 低位形态双确认，最高把握子集。点击' + (smartDualOnly ? '取消筛选' : '只看双确认')"
              >{{ smartDualOnly ? '✓ ' : '' }}双确认 {{ smartPoolResult.dual_confirm_count }} 只</el-tag>
              <el-tag
                v-if="smartPoolResult.excluded_severe_count"
                size="small" type="danger" effect="plain"
                title="命中七不买重度风险（回避级）已被风控剔除，不进推荐池"
              >风控剔除 {{ smartPoolResult.excluded_severe_count }} 只</el-tag>
              <el-tag
                v-if="smartPoolResult.timing_actionable_count"
                size="small" type="success" effect="dark"
                title="结构入选且盘中量价完成二次确认，目前仍在有效价格区间"
              >量价已确认 {{ smartPoolResult.timing_actionable_count }} 只</el-tag>
              <el-tag
                v-if="smartPoolResult.timing_watch_count"
                size="small" type="warning" effect="plain"
              >提前预警 {{ smartPoolResult.timing_watch_count }} 只</el-tag>
              <el-tag
                v-if="smartPoolResult.timing_wait_count"
                size="small" type="info" effect="plain"
              >等待确认 {{ smartPoolResult.timing_wait_count }} 只</el-tag>
              <el-tag
                v-if="smartPoolResult.timing_excluded_count"
                size="small" type="danger" effect="plain"
                title="命中重度风险或不可追入，已从最终推荐中剔除"
              >风控剔除 {{ smartPoolResult.timing_excluded_count }} 只</el-tag>
              <el-tag
                v-if="smartPoolResult.industry_concentration?.warning"
                size="small" type="warning" effect="plain"
                :title="smartPoolResult.industry_concentration.note"
              >行业集中 {{ smartPoolResult.industry_concentration.top_industry }} {{ smartPoolResult.industry_concentration.top_count }}只</el-tag>
              <el-tag
                v-if="realtimeBadge"
                size="small"
                :type="realtimeBadge.type"
                effect="plain"
                :title="realtimeBadge.title"
              >{{ realtimeBadge.text }}</el-tag>
              <el-tag
                v-if="smartPoolResult.intraday_candidate_count"
                size="small"
                type="primary"
                effect="plain"
                title="从强结构动态池中按实时量价重排最终名单"
              >动态池 {{ smartPoolResult.intraday_candidate_count }} 只</el-tag>
            </div>
            <div class="table-actions">
              <el-button size="small" @click="toggleSmartSelection">全选/取消</el-button>
              <el-button size="small" type="success" :disabled="!selectedSmartRows.length" @click="addSelectedToFavorites">
                加入自选({{ selectedSmartRows.length }})
              </el-button>
              <el-button size="small" type="primary" :disabled="!selectedSmartRows.length" @click="batchAnalyzeSelected">
                批量分析
              </el-button>
              <el-button size="small" type="success" plain @click="addAllSmartToFavorites">
                全部加入观察
              </el-button>
            </div>
            <div v-if="showPoolDesc" class="summary-meta">
              <span>自动候选 {{ smartPoolResult.universe_size }} 只</span>
              <span v-if="smartPoolResult.analyzed">已分析 {{ smartPoolResult.analyzed }} 只</span>
              <el-tag v-if="smartPoolResult.timing_confirmed_count" size="small" type="success" effect="plain">
                量价确认 {{ smartPoolResult.timing_confirmed_count }} 只
              </el-tag>
              <el-tag v-if="smartPoolResult.daily_as_of" size="small" type="info" effect="plain">
                日K {{ smartPoolResult.daily_as_of }}
              </el-tag>
              <el-tag
                v-if="smartPoolResult.timing_gate"
                size="small"
                :type="smartPoolResult.timing_gate.is_current ? 'success' : 'warning'"
                effect="plain"
              >
                时机层 {{ smartPoolResult.timing_gate.is_current
                  ? (smartPoolResult.timing_gate.phase_label || '已更新')
                  : '等待最新扫描' }}
              </el-tag>
              <el-tag
                v-if="smartPoolResult.ai_factor"
                size="small"
                :type="smartPoolResult.ai_factor.status === 'ready' ? 'success' : 'warning'"
                effect="plain"
              >
                AI因子 {{ smartPoolResult.ai_factor.status === 'ready' ? `已接入 ${smartPoolResult.ai_factor.pick_date || ''}` : '后台计算中' }}
              </el-tag>
            </div>
          </div>
          <div v-if="smartPoolResult?.items.length" class="decision-context">
            <div
              v-if="smartPoolResult.position_gate?.label"
              class="env-gate"
              :class="'env-' + (smartPoolResult.position_gate.state === '偏冷' ? 'cold' : smartPoolResult.position_gate.state === '偏暖' ? 'warm' : 'neutral')"
            >
              <span class="env-gate-head">环境仓位 · <b>{{ smartPoolResult.position_gate.label }}</b></span>
              <span class="env-gate-note" :title="smartPoolResult.position_gate.note">{{ smartPoolResult.position_gate.note }}</span>
            </div>
            <!-- 最近完整日线冻结结构底池；盘中实时量价负责动态换榜、确认与追高拦截。 -->
            <div v-if="smartPoolResult.list_basis?.as_of" class="basis-note">
              <b>结构底池 {{ smartPoolResult.list_basis.as_of }}</b>
              <span v-if="smartPoolResult.list_basis.same_count != null">
                · 较上期重合
                <b :class="{ 'basis-high': basisOverlapHigh }">
                  {{ smartPoolResult.list_basis.same_count }}/{{ smartPoolResult.list_basis.total }}
                </b>
              </span>
              <span class="basis-tip">
                · {{ smartPoolResult.list_basis.candidate_count || smartPoolResult.intraday_candidate_count || 10 }}
                只候选实时重排，<b>≥ {{ smartScoreFloor }} 分的 {{ smartPoolResult.items.length }} 只自动上榜</b>
              </span>
            </div>
            <!-- 结构层有历史回放，新增时机层仍需独立留痕；两者证据边界必须清楚。 -->
            <div class="basket-note">
              <div class="basket-head">
                <b>绿色表示量价已二次确认</b>
                <span>结构入选后须再通过实时量价确认；预警和等待状态不直接入场。</span>
                <a class="desc-toggle" @click="showBasketEvidence = !showBasketEvidence">
                  {{ showBasketEvidence ? '收起依据' : '查看依据' }}
                </a>
              </div>
              <div v-show="showBasketEvidence" class="basket-evidence">
                <div class="basket-usage">
                  <b>状态含义：</b>“量价已确认”表示结构与实时量价双双满足；“提前预警”和“等待确认”尚未满足第二重条件。
                  收盘复盘候选必须等下一交易日再次确认。
                </div>
                <ul>
                  <li>
                    以下统计只证明<b>底层结构候选</b>，不等于新增时机层已经证明能提升收益；
                    时机层从本次上线后单独留痕，样本足够后再比较。
                  </li>
                  <li>
                    旧结构池名次没有区分度：第 1-5 名中位超额 −0.44pp、胜率 47.8%，
                    反而低于第 6-10 名的 +0.64pp / 52.8%。量化分决定谁进名单，不决定谁涨得多。
                  </li>
                  <li>
                    收益集中在每期最强的 1-2 只：篮子平均超额 +1.75pp，
                    剔除每期最强 1 只只剩 +0.42pp，剔除 2 只转为 −0.50pp。
                  </li>
                  <li>所以买几只<b>不改变期望，只改变拿到结果的概率</b>：</li>
                </ul>
                <table class="basket-table">
                  <thead>
                    <tr><th>买几只</th><th>期望超额</th><th>中位超额</th><th>跑赢大盘概率</th><th>抓到那只大涨的概率</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="r in BASKET_SIM" :key="r.k" :class="{ 'row-bad': r.k === 1, 'row-good': r.k === 20 }">
                      <td>{{ r.k }} 只</td><td>{{ r.mean }}</td><td>{{ r.median }}</td>
                      <td><b>{{ r.beat }}</b></td><td>{{ r.tail }}</td>
                    </tr>
                  </tbody>
                </table>
                <p class="basket-src">
                  底层结构模型旧口径：12 个月 / 36 期 / 每期 top20 / 次日开盘买入 → T+5 收盘 /
                  超额对当期全市场中位（回放 anchor 2026-07-25，
                  <code>experiments/median_ab.py</code>）。历史统计不代表未来收益。
                </p>
              </div>
            </div>
          </div>

          <!-- 保底名单：今天一只都没够 90 分，给最强的几只但必须说清没达标。 -->
          <el-alert
            v-if="smartPoolResult?.score_floor_fallback"
            type="warning" :closable="false" show-icon class="few-picks-tip"
            :title="smartPoolResult.score_floor_note"
            description="未达标不等于能买：这是「今天最强的几只」，不是「达到入选标准的几只」。建议只跟踪观察，仓位交给顶部环境提示。"
          />

          <el-alert
            v-if="smartPoolResult && !smartPoolResult.score_floor_fallback && smartPoolResult.items.length > 0 && smartPoolResult.items.length < 5"
            type="info" :closable="false" show-icon class="few-picks-tip"
            :title="`当前市场环境下达标标的仅 ${smartPoolResult.items.length} 只`"
            description="评分阈值不随行情放宽（保持口径诚实）。弱市达标少是正常现象，可参考顶部大盘环境提示控制仓位，或等待市场转暖。"
          />
          <el-table
            v-if="smartPoolResult?.items.length"
            ref="smartTableRef"
            class="smart-pool-table"
            :data="smartDisplayItems"
            size="small"
            @selection-change="handleSmartSelectionChange"
          >
            <el-table-column type="selection" width="36" fixed />
            <el-table-column label="综合排序" width="82" fixed>
              <template #default="{ row }">
                <b>{{ Number(row.quality_score ?? row.score).toFixed(1) }}</b>
                <div class="score-pct">仅用于本次排序</div>
              </template>
            </el-table-column>
            <el-table-column label="结构分" width="82">
              <template #default="{ row }">
                <b>{{ displayScore(row) }}</b>
                <!-- 结构因子分全市场上限约 79.6，所以「79 分」是前 0.1% 而非「只有七八十分」。
                     分数本身不可横向解读，百分位可以，故一并显示。 -->
                <div v-if="row.score_percentile != null" class="score-pct">
                  全市场前 {{ row.score_percentile < 1 ? row.score_percentile.toFixed(1) : Math.round(row.score_percentile) }}%
                </div>
              </template>
            </el-table-column>
            <el-table-column label="时机确认" width="104">
              <template #default="{ row }">
                <el-tag
                  size="small"
                  :type="timingTagType(row)"
                  :effect="row.timing_actionable ? 'dark' : 'plain'"
                >
                  {{ row.timing_actionable ? '量价已确认' : (row.timing_label || '等待扫描') }}
                </el-tag>
                <div v-if="row.timing_score != null" class="score-pct">
                  时机 {{ Number(row.timing_score).toFixed(0) }}分
                </div>
              </template>
            </el-table-column>
            <el-table-column label="AI因子" width="64">
              <template #default="{ row }">
                <el-tag v-if="row.ai_factor_score" size="small" type="success" effect="plain">
                  {{ Number(row.ai_factor_score).toFixed(0) }}
                </el-tag>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <!-- 五方判读那一列已撤（2026-08-06）：它的输入就是本表已经展示过的那些
                 本地指标，不带来新信息；又从未被验证过命中率；LLM 单线程补打导致大多数
                 时候显示「—」，反而像数据出错。功能保留在个股深研，并转入留痕+复盘：
                 后台每日给当日名单打分入库，攒够样本后用 experiments/panel_eval.py
                 量它有没有预测力 —— 有就加权重，没有就删干净。 -->
            <el-table-column prop="symbol" label="代码" width="80" />
            <el-table-column label="名称" width="116">
              <template #default="{ row }">
                <span>{{ row.name }}</span>
                <el-tag v-if="row.triple_confirm" size="small" type="danger" effect="dark" class="dc-tag" title="结构因子 + 低位形态 + 相对强度 三重确认">三重</el-tag>
                <el-tag v-else-if="row.dual_confirm" size="small" type="success" effect="dark" class="dc-tag" title="结构因子 + 低位形态 双确认">双确认</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="行业/板块" width="110">
              <template #default="{ row }">{{ row.industry || row.board || '-' }}</template>
            </el-table-column>
            <el-table-column label="现价" width="72"><template #default="{ row }">{{ formatNumber(row.close) }}</template></el-table-column>
            <el-table-column label="涨跌幅" width="72" align="right">
              <template #default="{ row }">
                <span :class="changeClass(row.pct_chg)">{{ signedPercent(row.pct_chg) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="价位参考" width="156">
              <template #default="{ row }">
                <div v-if="row.timing_status === 'blocked'" class="trade-plan-cell risk-paused">
                  <el-tag size="small" type="danger" effect="dark">不可追入</el-tag>
                  <span>已涨停或距离涨停过近</span>
                  <em>等待回落并重新出现量价确认</em>
                </div>
                <div
                  v-else-if="['pending', 'unconfirmed', 'watch'].includes(row.timing_status || 'pending')"
                  class="trade-plan-cell risk-paused"
                >
                  <el-tag size="small" :type="row.timing_status === 'watch' ? 'warning' : 'info'" effect="plain">
                    {{ row.timing_label || '等待量价确认' }}
                  </el-tag>
                  <span>暂不显示买入价格</span>
                  <em>结构入选不等于当前可买，等待时机层确认</em>
                </div>
                <div v-else-if="row.timing_status === 'confirmed' && !row.timing_actionable" class="trade-plan-cell risk-paused">
                  <el-tag size="small" type="warning" effect="plain">{{ row.timing_label }}</el-tag>
                  <span>下一交易日重新确认</span>
                  <em>收盘复盘或历史触发信号不能按原价格追入</em>
                </div>
                <div v-else-if="buyLocked(row)" class="trade-plan-cell risk-paused">
                  <el-tag size="small" type="danger" effect="dark">暂停新增买入</el-tag>
                  <span>当前仅作为观察名单</span>
                  <em>市场风险降至警惕/安全后再显示买入计划</em>
                </div>
                <div v-else-if="riskLocked && row.daytrade_ok" class="trade-plan-cell daytrade">
                  <el-tag size="small" type="warning" effect="dark">T+1 口径</el-tag>
                  <span v-if="row.trade_plan?.buy_price">触发 <b>{{ formatNumber(row.trade_plan.buy_price) }}</b></span>
                  <span v-if="row.trade_plan?.stop_loss" class="tp-stop">
                    下沿 {{ formatNumber(row.trade_plan.stop_loss) }}
                  </span>
                  <em class="tp-warn">{{ row.daytrade_note }}</em>
                </div>
                <div v-else-if="row.trade_plan && row.trade_plan.buy_price" class="trade-plan-cell">
                  <el-tag v-if="row.timing_actionable" size="small" type="success" effect="dark" class="limit-up-tag">
                    盘中量价已确认
                  </el-tag>
                  <!-- 逆势大跌即使结构分较高，也必须先通过时机层；通过后仍保留风险提示。 -->
                  <el-tooltip v-if="row.entry_warning" effect="dark" placement="left"
                              :content="row.entry_warning.note">
                    <el-tag size="small" type="danger" effect="dark" class="limit-up-tag">
                      {{ row.entry_warning.text }} · 需持满5日
                    </el-tag>
                  </el-tooltip>
                  <template v-if="row.timing_actionable && row.radar_signal?.entry_low">
                    <span>
                      区间 <b>{{ formatNumber(row.radar_signal.entry_low) }}–{{ formatNumber(row.radar_signal.entry_high) }}</b>
                    </span>
                    <span class="tp-stop">失效 {{ formatNumber(row.radar_signal.invalidation_price) }}</span>
                    <span class="tp-target">上限 {{ formatNumber(row.radar_signal.chase_limit) }}</span>
                    <em>有效至 {{ row.radar_signal.valid_until?.slice(11, 16) || '本次扫描区间' }}</em>
                  </template>
                  <template v-else>
                    <span>触发 <b>{{ formatNumber(row.trade_plan.buy_price) }}</b></span>
                    <span class="tp-stop">下沿 {{ formatNumber(row.trade_plan.stop_loss) }}（{{ row.trade_plan.stop_loss_pct }}%）</span>
                    <span class="tp-target">上沿 {{ formatNumber(row.trade_plan.take_profit) }}（+{{ row.trade_plan.take_profit_pct }}%）</span>
                    <em>盈亏比 {{ row.trade_plan.risk_reward_ratio ?? '-' }}:1 · {{ row.trade_plan.basis === 'atr' ? 'ATR' : '比例' }}</em>
                  </template>
                </div>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column label="形态 · 强度" min-width="250">
              <template #default="{ row }">
                <el-tag v-if="row.confluence_bonus" class="capability-tag" type="warning" effect="dark"
                  :title="`结构因子之上，形态/强度共振加成 +${row.confluence_bonus}（已计入排序，量化分保持结构分不动）`">
                  共振+{{ row.confluence_bonus }}
                </el-tag>
                <el-tag v-for="pattern in (row.patterns || []).slice(0, MAX_PATTERN_TAGS)" :key="pattern.key || pattern.name"
                  class="capability-tag"
                  :type="pattern.category === '三不卖' ? 'success' : 'primary'"
                  :effect="pattern.category === '三不卖' ? 'dark' : 'plain'"
                  :title="pattern.reason">
                  <template v-if="pattern.category === '三不卖'">🔒 </template>{{ pattern.name }} {{ formatScore(pattern.strength) }}
                </el-tag>
                <!-- 超出的形态折进一个 +N：一行动辄十几个标签是行高失控的主因，
                     而排序只看分数，标签是佐证，全部铺开反而让一屏看不到几只票。 -->
                <el-tag v-if="(row.patterns || []).length > MAX_PATTERN_TAGS" class="capability-tag more-tag" effect="plain"
                  :title="(row.patterns || []).slice(MAX_PATTERN_TAGS).map(p => `${p.name} ${formatScore(p.strength)}`).join('\n')">
                  +{{ (row.patterns || []).length - MAX_PATTERN_TAGS }}
                </el-tag>
                <el-tag v-if="row.strength && row.strength.ema_stack" class="capability-tag" type="success" effect="plain"
                  title="站上 EMA8 且 EMA21、且多头排列（强度确认）">EMA多头</el-tag>
                <el-tag v-else-if="row.strength && row.strength.above_ema8 && row.strength.above_ema21" class="capability-tag" effect="plain"
                  title="站上 EMA8 与 EMA21（趋势确认）">站上双均线</el-tag>
                <el-tag v-if="row.strength && row.strength.dist_from_low != null" class="capability-tag" effect="plain"
                  title="距 250 日最低点涨幅（已证明的上升趋势）">距低点 +{{ Math.round(row.strength.dist_from_low) }}%</el-tag>
                <span v-if="!row.confluence_bonus && !(row.patterns || []).length && !row.strength" class="panel-pending">—</span>
              </template>
            </el-table-column>
            <el-table-column label="入选理由" min-width="300">
              <template #default="{ row }">
                <el-tooltip v-for="f in row.risk_flags || []" :key="f.key" :content="f.reason" placement="top">
                  <el-tag class="capability-tag" :type="f.level === 'risk' ? 'danger' : 'warning'" effect="dark">
                    ⚠ {{ f.name }}
                  </el-tag>
                </el-tooltip>
                <el-tag v-for="reason in (row.reasons || []).slice(0, MAX_REASON_TAGS)" :key="reason" class="capability-tag" effect="plain">
                  {{ reason }}
                </el-tag>
                <el-tag v-if="(row.reasons || []).length > MAX_REASON_TAGS" class="capability-tag more-tag" effect="plain"
                  :title="(row.reasons || []).slice(MAX_REASON_TAGS).join('\n')">
                  +{{ (row.reasons || []).length - MAX_REASON_TAGS }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="170" fixed="right">
              <template #default="{ row }">
                <el-button type="primary" link size="small" @click="addOneToFavorites(row)">加入自选</el-button>
                <el-button link type="primary" size="small" @click="openChart(row)">看图</el-button>
                <el-button link type="primary" size="small" @click="openWhy(row, 'smart')">理由</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else-if="!smartPoolLoading" description="点击一键智能推荐后生成量化股票池" />
        </section>
      </div>

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
defineOptions({ name: 'QuantPage' })  // keep-alive 保活标识，勿改
import { ElMessage, ElMessageBox } from 'element-plus'
import { DataLine, Search, TrendCharts } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'
import { favoritesApi } from '@/api/favorites'
import KLineProChart from '@/components/KLineProChart.vue'
import WhyPickedDrawer from '@/components/WhyPickedDrawer.vue'
import {
  quantApi,
  type QuantDataHealth,
  type QuantSmartPoolItem,
  type QuantSmartPoolResult,
  type QuantSmartPoolTask,
  type RiskAlert
} from '@/api/quant'
import { panelApi, type PanelScore } from '@/api/quant'

const route = useRoute()
const router = useRouter()

const dataHealth = ref<QuantDataHealth | null>(null)

const whyVisible = ref(false)
const whyRow = ref<any | null>(null)
const whyPool = ref<'smart' | 'pattern'>('smart')
const openWhy = (row: any, pool: 'smart' | 'pattern') => {
  whyRow.value = row
  whyPool.value = pool
  whyVisible.value = true
}

const healthLoading = ref(false)

// strategy 固定 balanced：短线波段模式 2026-08-19 从页面撤掉，后端分支保留，
// 想恢复只需把选择器加回模板，这里不必改。
// limit 现在只是后端候选池深度（engine 取 limit + 备胎），不再是用户看到的名单长度——
// 名单长度由综合排序分门槛决定，见 smartScoreFloor。
const smartPoolForm = ref({ limit: 20, universe_limit: 10000, strategy: 'balanced' })
const smartPoolLoading = ref(false)
const smartPoolResult = ref<QuantSmartPoolResult | null>(null)
const smartPoolTask = ref<QuantSmartPoolTask | null>(null)
const smartTableRef = ref<any>()
const selectedSmartRows = ref<QuantSmartPoolItem[]>([])
// ①c 双确认筛选：只看结构因子+低位形态双确认的最高把握子集
const smartDualOnly = ref(false)
// 入选门槛由后端给（LYNX_SMART_SCORE_FLOOR），拿不到时按默认 90 显示。
const smartScoreFloor = computed(() => Number(smartPoolResult.value?.score_floor ?? 90))
const smartDisplayItems = computed(() => {
  const items = smartPoolResult.value?.items || []
  return smartDualOnly.value ? items.filter((it) => it.dual_confirm) : items
})
const smartElapsed = ref(0)
let smartProgressTimer: number | undefined
let smartTaskTimer: number | undefined
let smartLiveTimer: number | undefined
let smartLiveRefreshing = false


const syncStatus = ref<any>({ running: false, phase: 'idle', done: 0, total: 0, errors_count: 0 })
const syncRunning = computed(() => !!syncStatus.value.running)
const syncPct = computed(() => syncStatus.value.total ? Math.floor(syncStatus.value.done / syncStatus.value.total * 100) : 0)
const isIntradayHealth = computed(() => dataHealth.value?.status === 'intraday')
const healthChipType = computed(() => {
  const status = dataHealth.value?.status
  if (status === 'fresh' || status === 'intraday') return 'success'
  if (status === 'partial_today' || status === 'stale_today') return 'warning'
  if (status === 'insufficient' || status === 'empty') return 'danger'
  return 'info'
})
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
      text: `留下综合排序 ≥ ${smartScoreFloor.value} 分的标的（不限只数，弱市达标少属正常）`,
      status: smartStepStatus(86, 100)
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
    // 数据同步页已并入数据中心，这里直接把人送过去，而不是留一句他不知道去哪点的提示
    ElMessage.warning('本地K线不足，已转到数据中心；请先完成一次历史日线重建。')
    router.push('/data-center')
    return false
  }
  if (health.needs_incremental_sync || health.sync_running || health.auto_started) {
    ElMessage.info('今日数据正在自动补齐；本次先使用最近完整交易日数据，完成后会自动刷新。')
  }
  return true
}

const riskAlert = ref<RiskAlert | null>(null)
const showPoolDesc = ref(false)
const showHealthDetail = ref(false)
const showBasketEvidence = ref(false)
// 每行最多铺多少个标签，其余折进「+N」（悬停可看全）。目的是在一屏里多显示几只票 ——
// 标签是佐证不是排序依据，全部铺开会把单行撑到 200px 以上，一屏只剩三四只。
const MAX_PATTERN_TAGS = 4
const MAX_REASON_TAGS = 3
// 重合度高说明这就是上一份名单，标红提示——避免被当成「今天又选中了同一批」
const basisOverlapHigh = computed(() => {
  const b = smartPoolResult.value?.list_basis
  if (!b || b.same_count == null || !b.total) return false
  return b.same_count / b.total >= 0.7
})

// 子集模拟结果（experiments/median_ab.py --profile，anchor 2026-07-25）。
// 写死是有意的：这是某一轮回放的研究结论，不随行情变化，做成接口反而暗示它会更新。
const BASKET_SIM = [
  { k: 1, mean: '+1.79pp', median: '-0.01pp', beat: '49.7%', tail: '15.4%' },
  { k: 3, mean: '+1.75pp', median: '+1.14pp', beat: '59.0%', tail: '38.8%' },
  { k: 5, mean: '+1.76pp', median: '+1.46pp', beat: '63.8%', tail: '55.8%' },
  { k: 10, mean: '+1.75pp', median: '+1.62pp', beat: '69.3%', tail: '78.7%' },
  { k: 20, mean: '+1.75pp', median: '+1.60pp', beat: '77.8%', tail: '91.7%' },
]
const riskLocked = computed(() => ['危险', '极危'].includes(riskAlert.value?.level || ''))
const realtimeBadge = computed(() => {
  const result = smartPoolResult.value
  if (!result?.realtime_status) return null
  const coverage = `${result.realtime_quote_count || 0}/${result.realtime_quote_total || 0}`
  if (result.realtime_status === 'live') {
    return { type: 'success' as const, text: `实时 ${result.realtime_as_of || ''}`, title: '交易时段每30秒按最新量价自动刷新' }
  }
  if (result.realtime_status === 'partial') {
    return { type: 'warning' as const, text: `实时覆盖 ${coverage}`, title: '部分候选行情未返回；未覆盖股票仅按结构分排序，不使用旧行情冒充实时数据' }
  }
  if (result.realtime_status === 'snapshot') {
    return { type: 'info' as const, text: `行情快照 ${result.realtime_as_of || ''}`, title: '当前不在连续交易时段，展示最近行情快照与收盘复盘状态' }
  }
  return { type: 'danger' as const, text: '实时行情不可用', title: '当前仅展示结构候选，不提供实时买入判断' }
})
const timingTagType = (row: QuantSmartPoolItem) => {
  if (row.timing_status === 'blocked') return 'danger'
  if (row.timing_actionable) return 'success'
  // hot_limit（涨停/近板）照常上榜，用橙色提示买入难度，不当成拦截。
  if (['confirmed', 'watch', 'hot_limit'].includes(row.timing_status || '')) return 'warning'
  return 'info'
}
// ③b 危险市况下不再整池锁死：后端标了 daytrade_ok 的逆势票仍给买入计划，但限定 T+1。
// 依据是回测——弱市里这类票 T+1 超额 +1.06pp/胜率 62%，T+2 起衰减、T+5 转负，
// 所以「放开但限期」比「一律不给」和「照常推荐」都更贴近真实赔率。
const buyLocked = (row: any) => riskLocked.value && !row?.daytrade_ok
const pct = (v?: number | null) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)

// 各池近30日 T+5 真实胜率（来自选股留痕），样本不足时提示积累中
const poolStats = ref<Record<string, { win_rate: number | null; samples: number }>>({})
const poolWinText = (pool: string) => {
  const s = poolStats.value[pool]
  if (!s || !s.samples || s.win_rate == null) return '真实胜率统计积累中 · 查看复盘'
  return `近30日 T+5 胜率 ${(s.win_rate * 100).toFixed(0)}%（${s.samples} 样本）`
}

const refreshSmartPoolLive = async () => {
  if (
    smartLiveRefreshing
    || smartPoolLoading.value
    || document.hidden
  ) return
  smartLiveRefreshing = true
  try {
    const result = await quantApi.smartPool(
      smartPoolForm.value.limit,
      smartPoolForm.value.universe_limit,
      true,
    )
    if (result?.items?.length) smartPoolResult.value = result
  } catch {
    // 静默刷新失败时保留上一份可用名单，用户手动点击仍会得到明确错误。
  } finally {
    smartLiveRefreshing = false
  }
}

// 吸顶两层（工具条 / 表头）：表头要停在工具条的正下方。工具条高度会随窗口宽度换行而变，
// 写死会露缝或盖住一行，所以量出来喂给 CSS 变量。
// （原来是三层，最上面那层是页签头；页签已删，--tabs-head-h 恒为 0，保留变量是因为
//   下面的 sticky 规则还在用它做偏移基准。）
const tabsHeadHeight = ref(0)
const stickyHeadHeight = ref(48)
let stickyHeadObserver: ResizeObserver | undefined

const observeStickyHead = () => {
  const hero = document.querySelector('.smart-hero')
  if (!(hero instanceof HTMLElement)) return
  const measure = () => {
    tabsHeadHeight.value = 0
    stickyHeadHeight.value = hero.getBoundingClientRect().height
  }
  measure()
  stickyHeadObserver = new ResizeObserver(measure)
  stickyHeadObserver.observe(hero)
}

onMounted(async () => {
  observeStickyHead()
  // 结构底池每天生成一次；页面打开后和停留期间每 30 秒按实时量价静默换榜。
  refreshSmartPoolLive()
  smartLiveTimer = window.setInterval(refreshSmartPoolLive, 30_000)
  quantApi.riskAlert().then((alert) => { riskAlert.value = alert || null }).catch(() => {})
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
  stickyHeadObserver?.disconnect()
  if (syncTimer) window.clearTimeout(syncTimer)
  if (smartLiveTimer) window.clearInterval(smartLiveTimer)
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
      const _res = task.result
      const _exc = _res.excluded_severe_count || 0
      if (!_res.items.length) {
        // ③a 弱市/风控清空 → 说明为什么没有推荐。
        // 标题不再写死「今日高风险」：那和盘面总览的风险档位是两码事，写死会出现
        // 「这里说高风险、总览说安全」的自相矛盾。空池的真实原因是没有个股达标。
        const parts = [] as string[]
        if (_res.position_gate?.note) parts.push(_res.position_gate.note)
        if (_exc) parts.push(`另有 ${_exc} 只候选命中「七不买」重度风险，已被风控剔除（宁可没得选也不给雷票）。`)
        if (_res.score_floor_note) parts.push(_res.score_floor_note + '。')
        parts.push('评分阈值不随行情放宽、风控不给雷票——今日暂无达标推荐，建议观望或等市场转暖。')
        ElMessageBox.alert(parts.join('\n\n'), '今日无达标个股 · 暂无推荐', {
          confirmButtonText: '知道了', type: 'warning',
        }).catch(() => {})
      } else {
        ElMessage.success(`智能推荐完成：${_res.items.length} 只` + (_exc ? `（已剔除 ${_exc} 只雷票）` : ''))
      }
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

const toggleSmartSelection = () => {
  smartTableRef.value?.toggleAllSelection?.()
}

const batchAnalyzeSelected = () => {
  const symbols = selectedSmartRows.value.map(row => row.symbol).filter(Boolean)
  if (!symbols.length) {
    ElMessage.warning('请选择要批量分析的股票')
    return
  }
  router.push({ path: '/analysis/batch', query: { stocks: symbols.join(','), market: 'A股' } })
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
  gap: 12px;

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

.head-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
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

.risk-lock-alert {
  margin: 4px 0 8px;
}
.risk-lock-body {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
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

.smart-home {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.smart-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 10px;
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
  gap: 8px;
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

  .score-floor-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 10px;
    border: 1px solid var(--el-color-success-light-5);
    border-radius: 14px;
    background: var(--el-color-success-light-9);
    color: var(--el-color-success);
    font-size: 13px;
    cursor: help;

    b {
      font-size: 14px;
    }
  }
}

.smart-actions {
  /* 原来按钮/计数/胜率 chip 三行纵向堆叠，占 102px——是压缩后头部里最大的一块。
     改成横排后同样的信息只占一行。 */
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
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
  padding: 8px 10px;
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
.pos-badge { font-size: 10px; opacity: .85; margin-left: 2px; }
.pattern-cat-filter {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin: 4px 0 10px; font-size: 13px;
}
.pcf-label { color: var(--el-text-color-secondary); }
.pcf-hint { font-size: 12px; color: var(--el-text-color-placeholder); }

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
  .limit-up-tag {
    align-self: flex-start;
    margin-bottom: 2px;
  }
  .tp-warn { color: var(--el-color-danger); }
  &.risk-paused {
    color: var(--el-color-danger);
    em { color: var(--el-color-danger-light-3); }
  }
}

.mini-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 10px;
  margin-bottom: 8px;
  color: var(--el-text-color-secondary);

  > span, > b, > .el-tag { flex: 0 0 auto; white-space: nowrap; }
  b {
    color: var(--el-text-color-primary);
    font-size: 20px;
  }
}

.smart-summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 6px 10px;
  margin-bottom: 6px;
}

.summary-primary,
.summary-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 8px;
  min-width: 0;
}

.summary-primary b {
  font-size: 18px;
}

.summary-meta {
  grid-column: 1 / -1;
  padding-top: 5px;
  border-top: 1px dashed var(--el-border-color-lighter);
  font-size: 12px;
}

.few-picks-tip {
  margin-bottom: 8px;
}
.decision-context {
  display: grid;
  grid-template-columns: minmax(320px, .72fr) minmax(560px, 1.28fr);
  gap: 6px;
  margin-bottom: 6px;
}
.env-gate {
  display: flex; flex-wrap: nowrap; align-items: baseline; gap: 6px 10px;
  margin: 0; padding: 6px 10px; border-radius: 8px;
  border-left: 4px solid var(--el-border-color); font-size: 13px;
}
.env-gate-head { font-weight: 700; }
.env-gate-note {
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--el-text-color-secondary);
}
.env-gate.env-cold { background: rgba(239,35,42,.08); border-left-color: #ef232a; }
.env-gate.env-cold .env-gate-head { color: #ef232a; }
.env-gate.env-neutral { background: rgba(212,136,6,.10); border-left-color: #d48806; }
.env-gate.env-neutral .env-gate-head { color: #d48806; }
.env-gate.env-warm { background: rgba(14,159,90,.10); border-left-color: #0e9f5a; }
.env-gate.env-warm .env-gate-head { color: #0e9f5a; }
.basket-note {
  grid-column: 1 / -1;
  margin: 0; padding: 6px 10px; border-radius: 8px; font-size: 13px;
  background: rgba(64,158,255,.08); border-left: 4px solid var(--el-color-primary);
}
.basket-head { display: flex; flex-wrap: nowrap; align-items: baseline; gap: 6px 10px;
  b { color: var(--el-color-primary); }
  span {
    min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    color: var(--el-text-color-secondary);
  }
  .desc-toggle { flex-shrink: 0; }
}
.basis-note {
  margin: 0; padding: 6px 10px; border-radius: 8px; font-size: 13px;
  background: var(--el-fill-color-light); border-left: 4px solid var(--el-color-info);
  display: flex; flex-wrap: nowrap; align-items: baseline; gap: 4px 8px;
  > b, > span { flex-shrink: 0; }
  .basis-tip {
    min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    color: var(--el-text-color-secondary);
  }
  .basis-high { color: #e6a23c; }
}
.basket-usage {
  margin-bottom: 8px; padding: 6px 10px; border-radius: 6px; line-height: 1.6;
  background: rgba(230,162,60,.12); color: var(--el-text-color-regular);
  b { color: #b88230; }
}
.basket-evidence {
  margin-top: 8px;
  ul { margin: 0 0 8px; padding-left: 18px; }
  li { margin-bottom: 4px; color: var(--el-text-color-regular); line-height: 1.6; }
}
.basket-table {
  border-collapse: collapse; font-size: 12px; margin-bottom: 6px;
  th, td { border: 1px solid var(--el-border-color-lighter); padding: 3px 10px; text-align: right; }
  th { background: var(--el-fill-color-light); font-weight: 600; }
  td:first-child, th:first-child { text-align: left; }
  .row-bad td { color: #ef232a; }
  .row-good td { background: rgba(14,159,90,.10); }
}
.basket-src { margin: 0; font-size: 11px; color: var(--el-text-color-secondary); line-height: 1.5;
  code { font-size: 11px; } }
.dc-tag { margin-left: 6px; }
.dc-filter { cursor: pointer; user-select: none; }

.table-actions {
  display: flex;
  gap: 6px;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: nowrap;
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

  .smart-summary,
  .decision-context {
    grid-template-columns: 1fr;
  }

  .smart-summary .table-actions,
  .summary-meta,
  .basket-note {
    grid-column: 1;
  }

  .table-actions,
  .basket-head,
  .basis-note,
  .env-gate {
    flex-wrap: wrap;
    justify-content: flex-start;
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
:deep(.smart-pool-table .cell) { padding-left: 4px; padding-right: 4px; }
:deep(.el-table th.el-table__cell) { padding: 6px 0; }
:deep(.el-tag) { height: 20px; line-height: 18px; padding: 0 5px; font-size: 11px; }

.panel-div { margin-left: 2px; font-size: 11px; color: var(--el-text-color-secondary); }
.panel-pending { color: var(--el-text-color-placeholder); }
.panel-summary { margin: 0 0 10px; font-size: 13px; }
.panel-note { margin: 10px 0 0; font-size: 12px; color: var(--el-text-color-placeholder); }
.trade-plan-cell.daytrade { border-left: 2px solid var(--el-color-warning); padding-left: 6px; }
.score-pct { font-size: 11px; color: var(--el-text-color-placeholder); margin-top: 2px; }

/* 头部密度：原来标题+数据健康+池说明+风险横幅要吃掉 583px（视口 889px 的 66%），
   个股表格被挤到屏幕下半部。这里把纵向堆叠改成同行排布、长说明默认收起。 */
.head-line { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.head-line h1 { font-size: 19px; }
.head-line p { margin: 0 !important; font-size: 12px; }

.desc-toggle {
  margin-left: 8px; font-size: 12px; font-weight: 400;
  color: var(--el-color-primary); cursor: pointer;
}

.data-health.collapsed { display: block; padding: 5px 10px; }
.health-title { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.health-title span { font-size: 12px; color: var(--el-text-color-secondary); }

.risk-lock-alert { margin: 0 !important; }
.risk-lock-alert :deep(.el-alert__title) { width: 100%; }
.risk-lock-body { font-size: 12px; }
.risk-lock-body .risk-action {
  flex: 1; min-width: 0; color: var(--el-text-color-regular);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.compact-smart-hero h2 { font-size: 16px; margin-bottom: 0; white-space: nowrap; }
.compact-smart-hero p { margin: 4px 0 0; font-size: 12px; line-height: 1.6; }

/* —— 紧凑版面 ——
   页头堆了六层（更新提示 / 标签页 / 工具条 / 推荐计数 / 环境仓位+结构底池 / 入场说明），
   加上每行铺满标签，1080p 下一屏只能看到三只票。这里只压缩留白与行高，不删任何信息：
   标签超出部分折进「+N」（见 MAX_PATTERN_TAGS / MAX_REASON_TAGS），悬停仍可看全。 */
.decision-context { gap: 4px; margin-bottom: 4px; }
.env-gate, .basket-note { padding: 4px 10px; font-size: 12px; }
.basis-note { padding: 4px 10px; font-size: 12px; }
.summary-meta { gap: 4px 8px; padding-top: 3px; }
.pattern-cat-filter { margin: 2px 0 6px; }

.health-chip { max-width: 320px; overflow: hidden; text-overflow: ellipsis; }
.head-tags .desc-toggle { font-size: 12px; }
.data-health { margin-bottom: 8px; }

/* 单行高度：标签的外边距与行高是主因，表格纵向 padding 次之 */
.capability-tag { margin: 0 4px 3px 0; }
.more-tag { opacity: .7; cursor: help; }
.smart-pool-table :deep(.el-table__cell) { padding: 6px 0; }
.smart-pool-table :deep(.cell) { line-height: 1.45; }

/* 表头吸顶。表格不再限高（20 只全排在页面里，只留一条页面滚动条），滚到第 15 只时列名
   必须还在。el-table 默认 overflow:hidden，会把 sticky 锁死在自己盒子里，必须放开，
   滚动容器才轮得到 .el-main。 */
.smart-pool-table,
.smart-pool-table :deep(.el-table__inner-wrapper) { overflow: visible; }
.smart-pool-table :deep(.el-table__header-wrapper) {
  position: sticky;
  top: 0;
  z-index: 5;
  background: var(--el-bg-color);
}

/* top:0 会把表头钉在 .el-main 的内容框上沿，而它的 padding-top 那一段仍属于可滚动区，
   表格行会从表头上方漏出一条。窄屏那段被固定顶栏盖住，看不见；宽屏必须把这 18px
   （AppLayout 里 .content 的 padding-top）顶掉，让表头贴到滚动区裁切边上。
   断点跟 AppLayout 的 760px 保持一致，改那边记得同步。

   宽屏再叠两层：标签页贴裁切边，工具条（一键智能推荐按钮所在）贴标签页下沿，表头贴工具条
   下沿——滚到第 20 只时仍能直接换参数重跑。中间的推荐计数、环境仓位、入场说明看一遍就够，
   不占常驻空间，随行滚走。三层背景都必须不透明，否则行会从底下透出来。
   窄屏不叠：一屏就那么高，钉三层等于没地方看票。 */
@media (min-width: 761px) {
  .smart-hero {
    position: sticky;
    top: calc(-18px + var(--tabs-head-h, 39px));
    z-index: 7;
  }
  .smart-pool-table :deep(.el-table__header-wrapper) {
    top: calc(-18px + var(--sticky-head-h, 87px));
  }
}
</style>
