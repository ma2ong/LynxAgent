<template>
  <div class="capital-panel">
    <div class="page-head">
      <h1>资金面板</h1>
      <span class="as-of">{{ flowKind === 'industry' ? '行业板块' : flowKind === 'concept' ? '概念板块' : '个股' }} · 主力资金</span>
    </div>

    <div class="tabs">
      <button :class="{ on: tab === 'flow' }" @click="tab = 'flow'">资金流</button>
      <button :class="{ on: tab === 'lhb' }" @click="tab = 'lhb'">龙虎榜</button>
      <button :class="{ on: tab === 'cal' }" @click="tab = 'cal'">财经日历</button>
    </div>

    <!-- ===================== 资金流 ===================== -->
    <section v-show="tab === 'flow'">
      <div class="kpis" v-if="kpi">
        <div class="kpi">
          <div class="k"><span class="dot" style="background:var(--cp-up)"></span>主力净流入 TOP</div>
          <div class="v">{{ kpi.topName }}</div>
          <div class="s num" :class="kpi.topNet >= 0 ? 'up' : 'down'">{{ signed(kpi.topNet) }} 亿</div>
        </div>
        <div class="kpi">
          <div class="k"><span class="dot" style="background:var(--cp-blue)"></span>净流入合计</div>
          <div class="v num" :class="kpi.sum >= 0 ? 'up' : 'down'">{{ signed(kpi.sum) }} <small>亿</small></div>
          <div class="s">{{ flow.rows?.length || 0 }} 个{{ flowKind === 'individual' ? '股' : '板块' }}</div>
        </div>
        <div class="kpi">
          <div class="k"><span class="dot" style="background:var(--cp-down)"></span>上涨占比</div>
          <div class="v num">{{ kpi.upPct }}<small>%</small></div>
          <div class="s">{{ kpi.upCount }} 涨 / {{ kpi.downCount }} 跌</div>
        </div>
        <div class="kpi">
          <div class="k"><span class="dot" style="background:#e0922b"></span>{{ flowKind === 'individual' ? '最强涨幅' : '最强领涨' }}</div>
          <div class="v">{{ kpi.leadName }}</div>
          <div class="s up num">+{{ kpi.leadPct }}%</div>
        </div>
      </div>

      <div class="toolbar">
        <div class="cat">
          <button :class="{ on: flowKind === 'industry' }" @click="switchKind('industry')">行业</button>
          <button :class="{ on: flowKind === 'concept' }" @click="switchKind('concept')">概念</button>
          <button :class="{ on: flowKind === 'individual' }" @click="switchKind('individual')">个股</button>
        </div>
        <span class="grow"></span>
        <div class="view-seg">
          <button :class="{ on: view === 'table' }" @click="view = 'table'" title="表格视图">▤ 表格</button>
          <button :class="{ on: view === 'heat' }" @click="view = 'heat'" title="热力视图">▦ 热力</button>
        </div>
      </div>

      <div v-loading="flowLoading">
        <el-empty v-if="flow.empty" :description="flow.message || '暂无数据'" />

        <!-- 表格视图 -->
        <div v-else-if="view === 'table'" class="card">
          <table class="dt">
            <thead>
              <tr>
                <th class="rk">#</th>
                <th>{{ flowKind === 'individual' ? '个股' : '板块' }}</th>
                <th class="r">涨跌幅</th>
                <th class="r">主力净流入</th>
                <th class="r">{{ flowKind === 'individual' ? '最新价' : '家数' }}</th>
                <th v-if="flowKind !== 'individual'">领涨股</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in flow.rows" :key="i">
                <td class="rk">{{ i + 1 }}</td>
                <td>
                  <div class="sector">
                    <span class="bar-dot" :style="{ background: catColor(i) }"></span>
                    <div class="sname-wrap">
                      <span class="sname">{{ r.name }}</span>
                      <span v-if="flowKind === 'individual'" class="scode">{{ r.symbol }}</span>
                    </div>
                  </div>
                </td>
                <td class="r"><span class="pill" :class="(r.pct ?? 0) >= 0 ? 'u' : 'd'">{{ signed(r.pct) }}%</span></td>
                <td class="r">
                  <div class="flowcell">
                    <span class="fv num" :class="(r.net_yi ?? 0) >= 0 ? 'up' : 'down'">{{ signed(r.net_yi) }}</span>
                    <span class="track"><span class="mid"></span>
                      <span class="fill" :class="(r.net_yi ?? 0) >= 0 ? 'in' : 'out'" :style="{ width: barW(r.net_yi) + '%' }"></span>
                    </span>
                  </div>
                </td>
                <td class="r count">{{ flowKind === 'individual' ? fmtPrice(r.price) : r.companies }}</td>
                <td v-if="flowKind !== 'individual'">
                  <span class="leader" v-if="r.leader"><span class="nm">{{ r.leader }}</span><span class="lp up">+{{ r.leader_pct }}%</span></span>
                  <span v-else class="muted">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 热力视图 -->
        <div v-else class="grid">
          <div v-for="(r, i) in flow.rows" :key="i" class="fcard" :style="heatStyle(r.net_yi)">
            <span class="heat" :style="{ background: (r.net_yi ?? 0) >= 0 ? 'var(--cp-up)' : 'var(--cp-down)' }"></span>
            <div class="top">
              <span class="sname">{{ r.name }}</span>
              <span class="chg" :class="(r.pct ?? 0) >= 0 ? 'u' : 'd'">{{ signed(r.pct) }}%</span>
            </div>
            <div class="flow num" :class="(r.net_yi ?? 0) >= 0 ? 'up' : 'down'">{{ signed(r.net_yi) }}<span class="unit">亿</span></div>
            <div class="mbar"><i :style="{ width: barW(r.net_yi) + '%', background: (r.net_yi ?? 0) >= 0 ? 'var(--cp-up)' : 'var(--cp-down)' }"></i></div>
            <div class="foot">
              <span>{{ flowKind === 'individual' ? fmtPrice(r.price) : (r.companies + ' 家') }}</span>
              <span class="ld" v-if="r.leader">{{ r.leader }} <b>+{{ r.leader_pct }}%</b></span>
            </div>
          </div>
        </div>
      </div>
      <p class="foot-note">数据每 5 分钟刷新 · 主力净流入条按当前列表横向对比，红=净流入 绿=净流出。仅供研究参考，不构成投资建议。</p>
    </section>

    <!-- ===================== 龙虎榜 ===================== -->
    <section v-show="tab === 'lhb'">
      <div class="toolbar">
        <el-date-picker v-model="lhbDate" type="date" value-format="YYYY-MM-DD"
          placeholder="选择日期（默认最近交易日）" @change="loadLhb" />
        <span class="grow"></span>
        <span class="hint">点任一行查看席位明细</span>
      </div>
      <div v-loading="lhbLoading">
        <el-empty v-if="lhb.empty" :description="lhb.message || '暂无数据'" />
        <div v-else class="card">
          <table class="dt">
            <thead>
              <tr><th class="rk">#</th><th>个股</th><th class="r">涨跌幅</th><th class="r">净买额</th><th>上榜原因</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in lhb.rows" :key="i" class="click-row" @click="openSeats(r)">
                <td class="rk">{{ i + 1 }}</td>
                <td><div class="code-cell"><span class="nm">{{ r.name }}</span><span class="cd">{{ r.symbol }}</span></div></td>
                <td class="r"><span class="pill" :class="(r.pct ?? 0) >= 0 ? 'u' : 'd'">{{ signed(r.pct) }}%</span></td>
                <td class="r">
                  <div class="netbuy">
                    <span class="nv num" :class="(r.net_buy_yi ?? 0) >= 0 ? 'up' : 'down'">{{ signed(r.net_buy_yi) }}</span>
                    <span class="nbar"><i :style="{ width: lhbBarW(r.net_buy_yi) + '%', background: (r.net_buy_yi ?? 0) >= 0 ? 'var(--cp-up)' : 'var(--cp-down)' }"></i></span>
                  </div>
                </td>
                <td><div class="reason" v-html="highlightReason(r.reason)"></div></td>
                <td class="chev">›</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <el-drawer v-model="seatsOpen" :title="`${seats.symbol || ''} 龙虎榜席位`" size="42%">
        <div v-loading="seatsLoading" class="seats">
          <div class="seat-block">
            <h4 class="buy">买入前列</h4>
            <div v-for="(s, i) in (seats.buy || [])" :key="i" class="seat-row">
              <span class="si">{{ i + 1 }}</span><span class="snm">{{ s.name }}</span><span class="sv up num">{{ s.buy_yi }} 亿</span>
            </div>
          </div>
          <div class="seat-block">
            <h4 class="sell">卖出前列</h4>
            <div v-for="(s, i) in (seats.sell || [])" :key="i" class="seat-row">
              <span class="si">{{ i + 1 }}</span><span class="snm">{{ s.name }}</span><span class="sv down num">{{ s.sell_yi }} 亿</span>
            </div>
          </div>
        </div>
      </el-drawer>
    </section>

    <!-- ===================== 财经日历 ===================== -->
    <section v-show="tab === 'cal'">
      <div class="cal-filter">
        <button v-for="t in calTypeDefs" :key="t.key" class="chip" :class="[{ on: calTypes.includes(t.key) }, t.key]"
          @click="toggleCalType(t.key)">
          <span class="cd" :style="{ background: calTypes.includes(t.key) ? '#fff' : t.color }"></span>{{ t.label }}
        </button>
      </div>
      <div v-loading="calLoading">
        <el-empty v-if="cal.empty" :description="cal.message || '窗口内暂无事件'" />
        <div v-else class="cal">
          <div v-for="(grp, di) in calGroups" :key="di" class="day" :class="{ today: grp.date === today }">
            <div class="d-label">{{ grp.date }}<span v-if="grp.date === today" class="badge">今天</span></div>
            <div class="events">
              <div v-for="(e, ei) in grp.items" :key="ei" class="evt">
                <span class="type" :class="e.type">{{ typeLabel(e.type) }}</span>
                <span class="et">{{ e.title }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { quantApi } from '@/api/quant'

const tab = ref<'flow' | 'lhb' | 'cal'>('flow')
const today = new Date().toISOString().slice(0, 10)
const catPalette = ['#2f6bff', '#e0922b', '#7c5cff', '#16a06a', '#e5384d', '#0ea5b7', '#d6539b', '#5b8def']

const signed = (v: number | null | undefined) => (v == null ? '-' : (v > 0 ? '+' : '') + v)
const fmtPrice = (v: number | null | undefined) => (v == null ? '-' : Number(v).toFixed(2))
const catColor = (i: number) => catPalette[i % catPalette.length]

/* ---------------- 资金流 ---------------- */
const flowLoading = ref(false)
const flowKind = ref<'industry' | 'concept' | 'individual'>('industry')
const view = ref<'table' | 'heat'>('table')
const flow = ref<any>({ empty: true })

const maxFlow = computed(() => Math.max(1, ...(flow.value.rows || []).map((r: any) => Math.abs(r.net_yi ?? 0))))
const barW = (net: number) => Math.min(100, Math.abs(net ?? 0) / maxFlow.value * 100)
const heatStyle = (net: number) => {
  const w = barW(net) / 100
  const a = (0.05 + w * 0.10).toFixed(3)
  const c = (net ?? 0) >= 0 ? `229,56,77` : `22,160,106`
  return { background: `linear-gradient(180deg, rgba(${c},${a}), #fff 60%)` }
}
const kpi = computed(() => {
  const rows = flow.value.rows || []
  if (!rows.length) return null
  const top = rows.reduce((a: any, b: any) => ((b.net_yi ?? 0) > (a.net_yi ?? 0) ? b : a))
  const sum = Math.round(rows.reduce((s: number, r: any) => s + (r.net_yi ?? 0), 0))
  const upCount = rows.filter((r: any) => (r.pct ?? 0) > 0).length
  const lead = rows.reduce((a: any, b: any) => ((b.leader_pct ?? b.pct ?? 0) > (a.leader_pct ?? a.pct ?? 0) ? b : a))
  return {
    topName: top.name, topNet: Math.round((top.net_yi ?? 0) * 100) / 100,
    sum, upCount, downCount: rows.length - upCount,
    upPct: Math.round(upCount / rows.length * 100),
    leadName: lead.leader || lead.name,
    leadPct: lead.leader_pct ?? lead.pct ?? 0,
  }
})

async function loadFlow() {
  flowLoading.value = true
  try {
    if (flowKind.value === 'industry') flow.value = await quantApi.capitalIndustryFlow()
    else if (flowKind.value === 'concept') flow.value = await quantApi.capitalConceptFlow()
    else flow.value = await quantApi.capitalStockFlow(50)
  } catch {
    flow.value = { empty: true, message: '拉取失败，请重试' }
  } finally { flowLoading.value = false }
}
function switchKind(k: 'industry' | 'concept' | 'individual') {
  if (flowKind.value === k) return
  flowKind.value = k
  loadFlow()
}

/* ---------------- 龙虎榜 ---------------- */
const lhbLoading = ref(false)
const lhbDate = ref('')
const lhb = ref<any>({ empty: true })
const seatsOpen = ref(false)
const seatsLoading = ref(false)
const seats = ref<any>({})
const lhbMax = computed(() => Math.max(1, ...(lhb.value.rows || []).map((r: any) => Math.abs(r.net_buy_yi ?? 0))))
const lhbBarW = (v: number) => Math.min(100, Math.abs(v ?? 0) / lhbMax.value * 100)
const REASON_KW = /(涨幅偏离值|跌幅偏离值|换手率|日涨幅达到\d+%|连续三个交易日|连续\d个交易日|30%|20%|15%|前5只|前五只)/g
const highlightReason = (reason: string) =>
  (reason || '').replace(REASON_KW, '<span class="kw">$1</span>')

async function loadLhb() {
  lhbLoading.value = true
  try { lhb.value = await quantApi.dragonTiger(lhbDate.value) }
  catch { lhb.value = { empty: true, message: '拉取失败，请重试' } }
  finally { lhbLoading.value = false }
}
async function openSeats(row: any) {
  seatsOpen.value = true
  seatsLoading.value = true
  seats.value = { symbol: row.symbol }
  try { seats.value = await quantApi.dragonTigerSeats(row.symbol, lhb.value.date || '') }
  finally { seatsLoading.value = false }
}

/* ---------------- 财经日历 ---------------- */
const calLoading = ref(false)
const calTypeDefs = [
  { key: 'earnings', label: '财报披露', color: '#2f6bff' },
  { key: 'unlock', label: '限售解禁', color: '#e0922b' },
  { key: 'ipo', label: '新股申购', color: '#16a06a' },
]
const calTypes = ref(['earnings', 'unlock', 'ipo'])
const cal = ref<any>({ empty: true })
const typeLabel = (t: string) => (({ earnings: '财报', unlock: '解禁', ipo: '新股' }) as any)[t] || t

const calGroups = computed(() => {
  const events = cal.value.events || []
  const map = new Map<string, any[]>()
  for (const e of events) {
    if (!map.has(e.date)) map.set(e.date, [])
    map.get(e.date)!.push(e)
  }
  return [...map.entries()].map(([date, items]) => ({ date, items }))
})

async function loadCalendar() {
  calLoading.value = true
  try { cal.value = await quantApi.capitalCalendar(calTypes.value.join(','), 14) }
  catch { cal.value = { empty: true, message: '拉取失败，请重试' } }
  finally { calLoading.value = false }
}
function toggleCalType(k: string) {
  const i = calTypes.value.indexOf(k)
  if (i >= 0) calTypes.value.splice(i, 1)
  else calTypes.value.push(k)
  loadCalendar()
}

onMounted(() => { loadFlow(); loadLhb(); loadCalendar() })
</script>

<style scoped lang="scss">
.capital-panel {
  --cp-ink:#1f2937; --cp-ink2:#5b6573; --cp-ink3:#9aa3b0;
  --cp-line:#eceef2; --cp-line2:#e3e6eb;
  --cp-blue:#2f6bff; --cp-blue-soft:#eef3ff;
  --cp-up:#e5384d; --cp-up-soft:#fdeef0; --cp-down:#16a06a; --cp-down-soft:#e9f7f1;
  --cp-radius:12px; --cp-shadow:0 1px 2px rgba(16,24,40,.04),0 6px 18px -10px rgba(16,24,40,.12);
  --cp-mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  padding:18px 20px 40px;color:var(--cp-ink);font-size:13px;
}
.num{font-variant-numeric:tabular-nums;font-family:var(--cp-mono);letter-spacing:-.2px}
.up{color:var(--cp-up)} .down{color:var(--cp-down)} .muted{color:var(--cp-ink3)}

.page-head{display:flex;align-items:baseline;justify-content:space-between;margin:0 2px 14px}
.page-head h1{font-size:19px;font-weight:700;margin:0;letter-spacing:-.01em}
.page-head .as-of{font-size:12px;color:var(--cp-ink3)}

.tabs{display:flex;gap:4px;border-bottom:1px solid var(--cp-line2);margin-bottom:16px}
.tabs button{border:0;background:transparent;font:inherit;font-size:14px;font-weight:600;color:var(--cp-ink3);
  padding:10px 14px;cursor:pointer;position:relative;transition:.15s}
.tabs button.on{color:var(--cp-ink)}
.tabs button.on::after{content:"";position:absolute;left:14px;right:14px;bottom:-1px;height:2.5px;background:var(--cp-blue);border-radius:2px}

.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}
.kpi{background:#fff;border:1px solid var(--cp-line);border-radius:var(--cp-radius);padding:13px 15px;box-shadow:var(--cp-shadow)}
.kpi .k{font-size:11.5px;color:var(--cp-ink3);display:flex;align-items:center;gap:6px;margin-bottom:7px}
.kpi .dot{width:6px;height:6px;border-radius:50%}
.kpi .v{font-size:21px;font-weight:750;letter-spacing:-.02em;line-height:1.2}
.kpi .v small{font-size:13px;color:var(--cp-ink3);font-weight:600}
.kpi .s{font-size:11.5px;color:var(--cp-ink3);margin-top:3px}

.toolbar{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.toolbar .grow{flex:1}
.toolbar .hint{font-size:12px;color:var(--cp-ink3)}
.cat{display:inline-flex;gap:2px;background:#f1f3f7;border-radius:9px;padding:3px}
.cat button{border:0;background:transparent;font:inherit;font-size:12.5px;font-weight:600;color:var(--cp-ink2);
  padding:6px 18px;border-radius:6px;cursor:pointer;transition:.15s}
.cat button.on{background:#fff;color:var(--cp-ink);box-shadow:0 1px 2px rgba(16,24,40,.08)}
.view-seg{display:inline-flex;gap:2px;background:#f1f3f7;border-radius:9px;padding:3px}
.view-seg button{border:0;background:transparent;font:inherit;font-size:12px;font-weight:600;color:var(--cp-ink2);
  padding:6px 12px;border-radius:6px;cursor:pointer;transition:.15s}
.view-seg button.on{background:#fff;color:var(--cp-blue);box-shadow:0 1px 2px rgba(16,24,40,.08)}

.card{background:#fff;border:1px solid var(--cp-line);border-radius:var(--cp-radius);box-shadow:var(--cp-shadow);overflow:hidden}
table.dt{width:100%;border-collapse:collapse}
table.dt thead th{background:#fafbfc;color:var(--cp-ink3);font-weight:600;font-size:11.5px;text-align:left;
  padding:11px 14px;border-bottom:1px solid var(--cp-line2);white-space:nowrap}
table.dt thead th.r{text-align:right}
table.dt tbody td{padding:11px 14px;border-bottom:1px solid var(--cp-line);vertical-align:middle}
table.dt tbody tr:last-child td{border-bottom:0}
table.dt tbody tr:hover{background:#fafbfe}
.rk{color:var(--cp-ink3);font-size:12px;width:32px;font-variant-numeric:tabular-nums}
.r{text-align:right}
.sector{display:flex;align-items:center;gap:9px}
.sector .bar-dot{width:3px;height:20px;border-radius:2px;flex:none}
.sector .sname-wrap{display:flex;flex-direction:column;line-height:1.3}
.sector .sname{font-weight:600}
.sector .scode{font-size:11px;color:var(--cp-ink3);font-family:var(--cp-mono)}
.pill{display:inline-flex;align-items:center;justify-content:flex-end;min-width:58px;padding:2px 9px;border-radius:6px;
  font-weight:700;font-size:12.5px;font-variant-numeric:tabular-nums}
.pill.u{background:var(--cp-up-soft);color:var(--cp-up)} .pill.d{background:var(--cp-down-soft);color:var(--cp-down)}
.flowcell{display:flex;align-items:center;gap:10px;justify-content:flex-end}
.flowcell .fv{font-weight:700;min-width:72px;text-align:right}
.track{position:relative;width:92px;height:8px;background:#f0f2f5;border-radius:5px;overflow:hidden;flex:none}
.track .fill{position:absolute;top:0;bottom:0;border-radius:5px;max-width:50%}
.track .fill.in{right:50%;background:linear-gradient(90deg,#f7a6b0,var(--cp-up))}
.track .fill.out{left:50%;background:linear-gradient(90deg,var(--cp-down),#8fdcbd)}
.track .mid{position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:#d7dbe2}
.count{color:var(--cp-ink2);font-variant-numeric:tabular-nums}
.leader{display:inline-flex;align-items:center;gap:7px}
.leader .nm{background:#f3f5f9;border:1px solid var(--cp-line2);border-radius:6px;padding:2px 8px;font-size:12px;font-weight:600}
.leader .lp{font-size:12px;font-weight:700;font-variant-numeric:tabular-nums}

.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.fcard{position:relative;background:#fff;border:1px solid var(--cp-line);border-radius:var(--cp-radius);
  padding:14px;box-shadow:var(--cp-shadow);overflow:hidden;transition:.16s}
.fcard:hover{transform:translateY(-2px);box-shadow:0 2px 4px rgba(16,24,40,.05),0 16px 30px -16px rgba(16,24,40,.22)}
.fcard .heat{position:absolute;inset:0 auto 0 0;width:4px}
.fcard .top{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;gap:8px}
.fcard .sname{font-weight:700;font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fcard .chg{font-size:12px;font-weight:700;padding:1px 7px;border-radius:5px;font-variant-numeric:tabular-nums;flex:none}
.fcard .chg.u{background:var(--cp-up-soft);color:var(--cp-up)} .fcard .chg.d{background:var(--cp-down-soft);color:var(--cp-down)}
.fcard .flow{font-size:23px;font-weight:780;letter-spacing:-.03em}
.fcard .flow .unit{font-size:12px;font-weight:600;color:var(--cp-ink3);margin-left:3px;font-family:-apple-system,sans-serif}
.fcard .mbar{height:6px;background:#f0f2f5;border-radius:4px;margin:9px 0 11px;overflow:hidden}
.fcard .mbar i{display:block;height:100%;border-radius:4px}
.fcard .foot{display:flex;align-items:center;justify-content:space-between;font-size:11.5px;color:var(--cp-ink3);gap:8px}
.fcard .foot .ld{display:inline-flex;gap:5px;align-items:center;color:var(--cp-ink2);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fcard .foot .ld b{color:var(--cp-up);font-weight:700}

.foot-note{font-size:12px;color:var(--cp-ink3);margin-top:12px}

/* 龙虎榜 */
.click-row{cursor:pointer}
.code-cell{display:flex;flex-direction:column;line-height:1.35}
.code-cell .nm{font-weight:700;font-size:13px}
.code-cell .cd{font-size:11.5px;color:var(--cp-ink3);font-family:var(--cp-mono)}
.reason{color:var(--cp-ink2);font-size:12px;max-width:540px}
.reason :deep(.kw){color:var(--cp-up);font-weight:600}
.netbuy{display:flex;align-items:center;gap:9px;justify-content:flex-end}
.netbuy .nv{font-weight:700;min-width:54px;text-align:right}
.netbuy .nbar{width:68px;height:7px;background:#f0f2f5;border-radius:4px;overflow:hidden;flex:none}
.netbuy .nbar i{display:block;height:100%;border-radius:4px}
.chev{color:var(--cp-ink3);font-size:18px;text-align:right;width:20px}
.seats{display:flex;flex-direction:column;gap:18px}
.seat-block h4{margin:0 0 8px;font-size:13px}
.seat-block h4.buy{color:var(--cp-up)} .seat-block h4.sell{color:var(--cp-down)}
.seat-row{display:flex;align-items:center;gap:10px;padding:8px 10px;border-bottom:1px solid var(--cp-line)}
.seat-row .si{color:var(--cp-ink3);font-size:11px;width:18px;font-variant-numeric:tabular-nums}
.seat-row .snm{flex:1;font-size:12.5px}
.seat-row .sv{font-weight:700}

/* 财经日历 */
.cal-filter{display:flex;gap:8px;margin-bottom:18px;flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--cp-line2);background:#fff;border-radius:999px;
  padding:6px 13px;font-size:12.5px;font-weight:600;color:var(--cp-ink2);cursor:pointer;transition:.15s}
.chip .cd{width:8px;height:8px;border-radius:50%}
.chip.on{border-color:transparent;color:#fff}
.chip.on.earnings{background:#2f6bff} .chip.on.unlock{background:#e0922b} .chip.on.ipo{background:#16a06a}
.cal{position:relative;margin-left:8px}
.cal::before{content:"";position:absolute;left:6px;top:6px;bottom:6px;width:2px;background:var(--cp-line2)}
.day{margin-bottom:6px}
.day .d-label{display:flex;align-items:center;gap:10px;font-size:12px;color:var(--cp-ink3);font-weight:700;
  padding:8px 0 8px 26px;position:relative}
.day .d-label::before{content:"";position:absolute;left:0;width:14px;height:14px;border-radius:50%;
  background:#fff;border:3px solid var(--cp-blue)}
.day.today .d-label::before{border-color:var(--cp-down)}
.day .d-label .badge{font-weight:700;color:var(--cp-ink2);background:#fff;border:1px solid var(--cp-line2);border-radius:6px;padding:0 7px;font-size:11px}
.events{padding-left:26px;display:flex;flex-direction:column;gap:8px;margin-bottom:8px}
.evt{display:flex;align-items:center;gap:11px;background:#fff;border:1px solid var(--cp-line);border-radius:10px;
  padding:10px 13px;box-shadow:var(--cp-shadow)}
.evt .type{font-size:11px;font-weight:700;padding:2px 8px;border-radius:5px;flex:none}
.evt .type.unlock{background:var(--cp-up-soft);color:#c0392f}
.evt .type.ipo{background:var(--cp-down-soft);color:#0f7a4f}
.evt .type.earnings{background:var(--cp-blue-soft);color:var(--cp-blue)}
.evt .et{font-weight:600}

@media (max-width:980px){
  .kpis,.grid{grid-template-columns:repeat(2,1fr)}
  .capital-panel{padding:12px 8px}
  .reason{max-width:none}
}
</style>
