<template>
  <div class="kol-wrap">
    <div class="kol-page">
      <!-- 头部 -->
      <header class="masthead">
        <div class="mh-eyebrow">KOL DAILY · A 股舆情日报</div>
        <h1 class="mh-title">KOL 日报</h1>
        <p class="mh-stats" v-if="data">
          {{ data.date }} · 过去 {{ data.stats?.hours }} 小时
          <b>{{ data.stats?.kol_total }}</b> 位 KOL · <b>{{ data.stats?.post_total }}</b> 条内容
        </p>
        <div class="mh-row">
          <span v-if="data?.hottest" class="hottest">今日最热 · <b>{{ data.hottest.name }}</b> · {{ data.hottest.kol_count }} KOL</span>
          <el-button class="refresh" text :loading="loading" @click="load">刷新</el-button>
        </div>
      </header>

      <el-alert v-if="isMock" type="warning" :closable="false" show-icon class="mock-tip"
        title="当前为占位示例数据，非真实 KOL 观点；接入雪球/微博/推特采集后，来源将带原文链接。" />

      <div v-if="loading && !data" class="loading-hint">正在加载 KOL 日报…</div>

      <template v-if="data">
        <!-- ① 今日 KOL 关注度 -->
        <section class="rank">
          <div class="sec-head"><span class="sh-mark" />今日 KOL 关注度<em>TOP {{ data.attention_rank?.length }}</em></div>
          <button v-for="(r, i) in data.attention_rank" :key="r.code" type="button" class="rank-row" @click="openStock(r.code)">
            <span class="rk-no">{{ String(i + 1).padStart(2, '0') }}</span>
            <span class="rk-name">{{ r.name }}</span>
            <span class="rk-code">{{ r.code }}</span>
            <span class="rk-track"><i :style="{ width: barWidth(r.kol_count) }" /></span>
            <span class="rk-kol">{{ r.kol_count }} KOL · {{ r.post_count }} 帖</span>
          </button>
        </section>

        <!-- ② 个股观点（按分组） -->
        <section v-for="group in groupedStocks" :key="group.name" class="group">
          <div class="sec-head"><span class="sh-mark" />{{ group.name }}<em>{{ group.items.length }} 只</em></div>
          <article v-for="s in group.items" :key="s.code" class="card">
            <div class="card-head">
              <button type="button" class="ch-id" @click="openStock(s.code)">
                <span class="ch-name">{{ s.name }}</span>
                <span class="ch-code">{{ s.code }}</span>
              </button>
              <span class="ch-tag" :class="stanceClass(s.stance)">{{ s.tag }}</span>
              <span class="ch-count">{{ s.kol_count }} KOL · {{ s.post_count }} 帖</span>
            </div>
            <p class="card-summary">{{ s.summary }}</p>

            <div v-for="(b, bi) in s.view_blocks" :key="bi" class="vblock">
              <div class="vb-head">
                <span class="vb-dot" :class="kindClass(b.kind)" />
                <span class="vb-kind">{{ b.kind }}</span>
                <span class="vb-count">{{ b.count }} 条</span>
              </div>
              <p class="vb-text">{{ b.content }}</p>
              <div class="vb-src">
                <span class="src-lead">来源</span>
                <component
                  :is="src.url ? 'a' : 'span'" v-for="(src, si) in b.sources" :key="si"
                  class="src" :class="{ link: src.url }"
                  :href="src.url || undefined" :target="src.url ? '_blank' : undefined" rel="noopener"
                ><i>{{ src.platform }}</i>@{{ src.author }}</component>
                <span v-if="b.sources.some((x: any) => x.is_placeholder)" class="src-ph">占位</span>
              </div>
            </div>

            <button type="button" class="card-cta" @click="openStock(s.code)">查看 {{ s.name }} 深研报告 →</button>
          </article>
        </section>

        <!-- ③ 其他热议 -->
        <section v-if="data.other_topics?.length" class="group">
          <div class="sec-head"><span class="sh-mark warn" />其他热议<em>{{ data.other_topics.length }} 条</em></div>
          <article v-for="(t, i) in data.other_topics" :key="i" class="topic">
            <span class="tp-tag">{{ t.tag }}</span>
            <div class="tp-body">
              <div class="tp-title">{{ t.title }}</div>
              <p class="tp-text">{{ t.content }}</p>
              <div class="vb-src">
                <span class="src-lead">来源</span>
                <component
                  :is="src.url ? 'a' : 'span'" v-for="(src, si) in t.sources" :key="si"
                  class="src" :class="{ link: src.url }"
                  :href="src.url || undefined" :target="src.url ? '_blank' : undefined" rel="noopener"
                ><i>{{ src.platform }}</i>@{{ src.author }}</component>
              </div>
            </div>
          </article>
        </section>

        <p class="foot">{{ data.note }}</p>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ApiClient } from '@/api/request'

const router = useRouter()
const loading = ref(false)
const data = ref<any>(null)
const isMock = ref(false)

const stanceClass = (s: string) => (s === '看多' ? 'st-bull' : s === '看空' ? 'st-bear' : 'st-neutral')
const kindClass = (k: string) => {
  if (k.includes('买入')) return 'k-buy'
  if (k.includes('风险') || k.includes('卖出')) return 'k-risk'
  if (k.includes('数据')) return 'k-data'
  return 'k-view'
}

const maxKol = computed(() => Math.max(1, ...(data.value?.attention_rank || []).map((r: any) => r.kol_count || 0)))
const barWidth = (n: number) => `${Math.round((n / maxKol.value) * 100)}%`

const groupedStocks = computed(() => {
  const map = new Map<string, any[]>()
  for (const s of data.value?.stocks || []) {
    const g = s.group || '其他'
    if (!map.has(g)) map.set(g, [])
    map.get(g)!.push(s)
  }
  return Array.from(map.entries()).map(([name, items]) => ({ name, items }))
})

const openStock = (code: string) => router.push({ name: 'stock-analysis', query: { symbol: code } })

const load = async () => {
  loading.value = true
  try {
    const res: any = await ApiClient.get('/api/lite/kol/digest', { _ts: Date.now() }, { timeout: 15000 })
    data.value = res?.data || null
    isMock.value = !!res?.data?.is_mock
  } catch (e: any) {
    ElMessage.error(e?.message || '加载 KOL 日报失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped lang="scss">
.kol-wrap { width: 100%; }
.kol-page { max-width: 880px; margin: 0 auto; display: flex; flex-direction: column; gap: 22px; }

/* 头部 */
.masthead { text-align: center; padding: 10px 0 6px; }
.mh-eyebrow { font-size: 11px; letter-spacing: 2px; color: var(--el-color-primary); font-weight: 600; }
.mh-title { margin: 8px 0 6px; font-size: 30px; font-weight: 800; letter-spacing: 1px; }
.mh-stats { margin: 0; font-size: 13px; color: var(--el-text-color-secondary); b { color: var(--el-text-color-primary); } }
.mh-row { display: flex; align-items: center; justify-content: center; gap: 14px; margin-top: 10px; }
.hottest { font-size: 12px; color: var(--el-text-color-secondary); padding: 4px 12px; border-radius: 20px;
  background: var(--el-fill-color-light); b { color: var(--el-color-primary); } }
.refresh { font-size: 12px; }
.mock-tip { border-radius: 8px; }
.loading-hint { color: var(--el-text-color-secondary); font-size: 13px; padding: 20px 0; text-align: center; }

/* 分区标题 */
.sec-head { display: flex; align-items: center; gap: 9px; font-size: 15px; font-weight: 700; margin-bottom: 12px;
  .sh-mark { width: 4px; height: 15px; border-radius: 2px; background: var(--el-color-primary); }
  .sh-mark.warn { background: var(--el-color-warning); }
  em { font-style: normal; font-size: 12px; font-weight: 400; color: var(--el-text-color-placeholder); margin-left: 6px; }
}

/* ① 关注度 */
.rank { background: var(--el-fill-color-lighter); border: 1px solid var(--el-border-color-lighter); border-radius: 12px; padding: 8px 10px; }
.rank-row { width: 100%; display: flex; align-items: center; gap: 14px; cursor: pointer; padding: 10px 10px;
  border: none; background: transparent; font: inherit; color: inherit; text-align: left; border-radius: 8px; transition: background .15s ease; }
.rank-row + .rank-row { border-top: 1px solid var(--el-border-color-lighter); }
.rank-row:hover { background: var(--el-fill-color); }
.rk-no { font-size: 13px; font-weight: 700; color: var(--el-text-color-placeholder); font-variant-numeric: tabular-nums; width: 22px; }
.rank-row:nth-child(1) .rk-no, .rank-row:nth-child(2) .rk-no, .rank-row:nth-child(3) .rk-no { color: var(--el-color-primary); }
.rk-name { font-weight: 600; font-size: 14px; min-width: 84px; }
.rk-code { font-size: 12px; color: var(--el-text-color-placeholder); font-variant-numeric: tabular-nums; min-width: 56px; }
.rk-track { flex: 1; height: 8px; background: var(--el-fill-color); border-radius: 4px; overflow: hidden; max-width: 360px;
  i { display: block; height: 100%; background: linear-gradient(90deg, var(--el-color-primary-light-3), var(--el-color-primary)); border-radius: 4px; transition: width .5s ease; } }
.rk-kol { font-size: 12px; color: var(--el-text-color-secondary); white-space: nowrap; font-variant-numeric: tabular-nums; }

/* ② 个股卡 */
.group { display: flex; flex-direction: column; gap: 14px; }
.card { border: 1px solid var(--el-border-color-lighter); border-radius: 12px; padding: 18px 20px; background: var(--el-fill-color-blank);
  transition: border-color .15s ease, box-shadow .15s ease; }
.card:hover { border-color: var(--el-border-color); box-shadow: 0 2px 14px rgba(0,0,0,.05); }
.card-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ch-id { display: inline-flex; align-items: baseline; gap: 7px; cursor: pointer; background: none; border: none; padding: 0; }
.ch-name { font-size: 19px; font-weight: 800; }
.ch-code { font-size: 12px; color: var(--el-text-color-secondary); font-variant-numeric: tabular-nums; }
.ch-tag { font-size: 11px; font-weight: 700; padding: 1px 9px; border-radius: 4px; }
.ch-count { margin-left: auto; font-size: 12px; color: var(--el-text-color-placeholder); }
.card-summary { margin: 12px 0 14px; font-size: 14px; font-style: italic; color: var(--el-text-color-regular); line-height: 1.65; }

.vblock { background: var(--el-fill-color-lighter); border-radius: 9px; padding: 11px 14px; margin-bottom: 9px; }
.vb-head { display: flex; align-items: center; gap: 7px; }
.vb-dot { width: 7px; height: 7px; border-radius: 50%; }
.vb-dot.k-buy { background: #ef232a; }
.vb-dot.k-risk { background: #14b143; }
.vb-dot.k-data { background: var(--el-color-info); }
.vb-dot.k-view { background: var(--el-color-warning); }
.vb-kind { font-size: 12px; font-weight: 700; }
.vb-count { font-size: 11px; color: var(--el-text-color-placeholder); }
.vb-text { margin: 7px 0; font-size: 13px; color: var(--el-text-color-regular); line-height: 1.6; }
.vb-src { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; font-size: 11px; }
.src-lead { color: var(--el-text-color-placeholder); }
.src { color: var(--el-text-color-secondary); text-decoration: none;
  i { font-style: normal; font-weight: 600; color: var(--el-color-primary); margin-right: 2px; } }
.src.link:hover { text-decoration: underline; }
.src-ph { color: var(--el-text-color-placeholder); padding: 0 6px; border-radius: 3px; background: var(--el-fill-color); }

.card-cta { width: 100%; margin-top: 6px; padding: 9px; border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600;
  color: var(--el-color-primary); background: var(--el-color-primary-light-9); border: 1px solid transparent; transition: all .15s ease; }
.card-cta:hover { border-color: var(--el-color-primary-light-5); }

.st-bull { color: #fff; background: #ef232a; }
.st-bear { color: #fff; background: #14b143; }
.st-neutral { color: var(--el-text-color-regular); background: var(--el-fill-color); }

/* ③ 其他热议 */
.topic { display: flex; gap: 14px; padding: 14px 18px; border: 1px solid var(--el-border-color-lighter); border-radius: 12px; background: var(--el-fill-color-blank); }
.tp-tag { flex-shrink: 0; height: fit-content; font-size: 11px; font-weight: 700; padding: 3px 9px; border-radius: 4px; background: var(--el-fill-color); color: var(--el-text-color-secondary); }
.tp-body { flex: 1; }
.tp-title { font-size: 15px; font-weight: 700; }
.tp-text { margin: 5px 0 9px; font-size: 13px; color: var(--el-text-color-secondary); line-height: 1.55; }

.foot { font-size: 11px; color: var(--el-text-color-placeholder); text-align: center; margin: 8px 0 24px; }

@media (max-width: 720px) {
  .ch-count { margin-left: 0; }
  .mh-title { font-size: 24px; }
}
</style>
