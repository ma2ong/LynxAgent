<template>
  <div class="lifecycle-page" v-loading="loading">
    <!-- 不再重复写标题：这个组件嵌在「用户设置」页的 el-card 里，外壳已经给了
         「规则生命周期」和一句说明，组件自己再写一遍就是同一句话连着出现两次。 -->
    <div class="page-head">
      <p class="sub">判定来自 {{ data?.runs || 0 }} 轮审计结果，档位是看过判定之后人做的处置。</p>
      <el-button size="small" :loading="loading" @click="load">刷新</el-button>
    </div>

    <div class="counts">
      <span
        v-for="s in STAGES"
        :key="s"
        class="chip"
        :class="[s, { on: filter === s }]"
        @click="filter = filter === s ? '' : s"
      >
        {{ data?.stage_label?.[s] || s }} {{ data?.counts?.[s] ?? 0 }}
      </span>
    </div>

    <!-- 「已审待处置」是这张表唯一需要人动手的一档：机器已经给了判定，结论却还没
         落到任何地方。它不为零就说明上一轮审计没收尾。 -->
    <div v-if="(data?.counts?.unassigned || 0) > 0" class="todo">
      有 {{ data?.counts?.unassigned }} 条规则已经审完但没有处置。过闸的要么上线要么记明
      为什么不上，没过闸的要归档 —— 否则下次还会有人重新提一遍。
    </div>

    <!-- 这条警告不能省。表上各条的判定来自不同批次的审计，Holm 家族和入场口径都可能
         不同，并排读会得出错误结论——2026-08-31 就照着这张表把 sector_hot5 误判成
         优于生产口径，同批重跑后它其实是最差的那条。 -->
    <div class="cross-run">
      各条判定来自不同批次审计，<b>不能横向比数字</b>。要比较两条规则，必须让它们出现在
      同一次提交里（<code>--rule a --rule b</code>）；鼠标停在「同批」上可看哪些规则可比。
    </div>

    <div class="list">
      <div v-for="it in shown" :key="it.rule" class="card" :class="it.stage">
        <div class="row1">
          <span class="stage" :class="it.stage">{{ it.stage_label }}</span>
          <b class="name">{{ it.rule }}</b>
          <span v-if="it.latest" class="verdict" :class="it.latest.passed ? 'pass' : 'fail'">
            {{ it.latest.passed ? '七闸全过' : '未过：' + (it.latest.failed_gates || []).join(' / ') }}
          </span>
          <span v-else class="verdict muted">尚未审计</span>
          <span
            v-if="it.comparable?.length"
            class="cmp"
            :title="'与这些规则同出一次审计，可以横向比：' + it.comparable.join('、')"
          >同批 {{ it.comparable.length }}</span>
          <span class="audits">{{ it.audits }} 次审计</span>
        </div>

        <div v-if="it.latest" class="row2">
          <span>对照增量 <b :class="num(it.latest.inc_excess)">{{ fmt(it.latest.inc_excess) }}</b></span>
          <span>CI 下沿 <b :class="num(it.latest.inc_ci_lo)">{{ fmt(it.latest.inc_ci_lo) }}</b></span>
          <span>去右尾 <b :class="num(it.latest.inc_ex_tail)">{{ fmt(it.latest.inc_ex_tail) }}</b></span>
          <span>样本 {{ it.latest.samples ?? '—' }} / {{ it.latest.clusters ?? '—' }} 天</span>
          <span>稳定 {{ it.latest.stable_years ?? '—' }} 年</span>
          <!-- 入场口径必须显示：close 口径含用户吃不到的隔夜段，两者结论可以相反。
               老结果没记这个字段，如实显示「未记录」而不是默认当成 close。 -->
          <span class="entry" :class="{ warn: it.latest.entry !== 'open' }">
            口径 {{ entryLabel(it.latest.entry) }}
          </span>
          <span class="when">{{ it.latest.since }} 起 · T+{{ it.latest.horizon }}</span>
          <span class="run" :title="'判定出自 ' + it.verdict_run">{{ runLabel(it.verdict_run) }}</span>
        </div>

        <div v-if="it.stage_note" class="note">{{ it.stage_note }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ApiClient } from '@/api/request'

interface AuditRecord {
  entry: string; since: string; horizon: number
  samples: number | null; clusters: number | null
  inc_excess: number | null; inc_ci_lo: number | null; inc_ex_tail: number | null
  stable_years: number | null; passed: boolean; failed_gates: string[]
}
interface LifecycleItem {
  rule: string; stage: string; stage_label: string; stage_note: string
  audits: number; latest: AuditRecord | null
  verdict_run: string | null
  comparable: string[]
}
interface Lifecycle {
  runs: number
  counts: Record<string, number>
  stage_label: Record<string, string>
  items: LifecycleItem[]
}

const STAGES = ['production', 'observation', 'unassigned', 'candidate', 'rejected']
const data = ref<Lifecycle | null>(null)
const loading = ref(false)
const filter = ref('')

const shown = computed(() => {
  const items = data.value?.items || []
  return filter.value ? items.filter(it => it.stage === filter.value) : items
})

const fmt = (v: number | null | undefined) =>
  v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}`
const num = (v: number | null | undefined) => (v == null ? '' : v > 0 ? 'up' : 'down')
// 只显示审计批次的时间戳部分：文件名那一长串在卡片上纯占地方
const runLabel = (f: string | null) =>
  f ? f.replace('rule-audit-', '').replace('.json', '') : ''
const entryLabel = (e: string) =>
  e === 'open' ? '次日开盘（产品口径）' : e === 'close' ? '当日收盘（含隔夜段）' : '未记录'

const load = async () => {
  loading.value = true
  try {
    // /api/admin/* 一律包一层 {success, data, message}，与 /api/lite/* 的裸对象不同，
    // 这里必须解包再用——直接赋值会得到一张全 0 的空表而不是报错，很难发现。
    const resp = await ApiClient.get<{ data: Lifecycle }>('/api/admin/rule-lifecycle')
    data.value = resp?.data ?? null
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<style scoped>
/* 这块嵌在「用户设置」页的 el-card 里，那是浅色底 —— 原先整片深色卡片从页面里跳出来。
   配色统一走 Element 变量。 */
.lifecycle-page { padding: 4px; }
.page-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.page-head .el-button { margin-left: auto; }
.sub { margin: 0; color: var(--el-text-color-secondary); font-size: 12px; }
.counts { display: flex; gap: 8px; margin: 12px 0; flex-wrap: wrap; }
.chip { padding: 3px 10px; border-radius: 12px; font-size: 12px; cursor: pointer;
  background: var(--el-fill-color-light); color: var(--el-text-color-regular);
  border: 1px solid transparent; }
.chip.on { border-color: var(--el-color-primary); color: var(--el-color-primary); }
.chip.production { color: var(--el-color-danger); }
.chip.observation { color: var(--el-color-warning); }
.chip.unassigned { color: #b8860b; }
.chip.rejected { color: var(--el-text-color-placeholder); }
.todo { background: var(--el-color-warning-light-9); border: 1px solid var(--el-color-warning-light-5);
  color: var(--el-text-color-primary); padding: 8px 12px; border-radius: 6px; font-size: 12px; margin-bottom: 12px; }
.cross-run { background: var(--el-color-primary-light-9); border: 1px solid var(--el-color-primary-light-5);
  color: var(--el-text-color-primary); padding: 8px 12px; border-radius: 6px; font-size: 12px;
  margin-bottom: 12px; line-height: 1.6; }
.cross-run code { background: var(--el-fill-color); padding: 1px 5px; border-radius: 3px; }
.list { display: flex; flex-direction: column; gap: 8px; }
.card { background: var(--el-fill-color-blank); border: 1px solid var(--el-border-color-lighter);
  border-left: 3px solid var(--el-border-color); border-radius: 8px; padding: 10px 12px; }
.card.production { border-left-color: var(--el-color-danger); }
.card.observation { border-left-color: var(--el-color-warning); }
.card.unassigned { border-left-color: #d4a017; }
/* 已否决的用底色后退，不用 opacity——opacity 会把文字一起冲淡、糊成一团。 */
.card.rejected { background: var(--el-fill-color-lighter); border-left-color: var(--el-border-color-light); }
.card.rejected .name, .card.rejected .row2, .card.rejected .note { color: var(--el-text-color-secondary); }
.row1 { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.stage { font-size: 11px; padding: 1px 7px; border-radius: 3px;
  background: var(--el-fill-color); color: var(--el-text-color-regular); }
.stage.production { background: var(--el-color-danger-light-9); color: var(--el-color-danger); }
.stage.observation { background: var(--el-color-warning-light-9); color: var(--el-color-warning); }
.stage.unassigned { background: #fdf6e3; color: #b8860b; }
.name { font-family: ui-monospace, Menlo, Consolas, monospace; color: var(--el-text-color-primary); }
.verdict { font-size: 12px; }
.verdict.pass { color: var(--el-color-success); }
.verdict.fail { color: var(--el-text-color-secondary); }
.verdict.muted { color: var(--el-text-color-placeholder); }
.cmp { font-size: 11px; color: var(--el-color-primary); border: 1px solid var(--el-color-primary-light-7);
  border-radius: 3px; padding: 0 5px; cursor: default; }
.run { color: var(--el-text-color-placeholder); font-family: ui-monospace, Menlo, Consolas, monospace; cursor: default; }
.audits { margin-left: auto; font-size: 11px; color: var(--el-text-color-placeholder); }
.row2 { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 6px; font-size: 12px; color: var(--el-text-color-secondary); }
.row2 b { font-weight: 600; color: var(--el-text-color-primary); }
.entry.warn { color: var(--el-color-warning); }
.when { color: var(--el-text-color-placeholder); }
.note { margin-top: 6px; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.5; }
.up { color: var(--el-color-danger); }
.down { color: var(--el-color-success); }
</style>
