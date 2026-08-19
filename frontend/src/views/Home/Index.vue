<template>
  <div class="site" ref="root">
    <!-- 顶栏 -->
    <header class="nav">
      <div class="wrap nav-inner">
        <!-- 用 App 里那个真的品牌徽标，不要另造一个。落地页原来自己画了个黑方块写字母 A，
             和产品内红色渐变徽标是两个牌子，访客点进应用会以为跳错站了。 -->
        <a class="brand" href="#top">
          <BrandLogo :size="30" />
          <span class="brand-text">AStockPick<em>A股优选</em></span>
        </a>
        <nav class="links">
          <a href="#features">功能</a>
          <a href="#method">方法</a>
          <a href="#data">数据</a>
          <a href="#rejected">否决记录</a>
          <a href="#pricing">定价</a>
        </nav>
        <div class="nav-cta">
          <template v-if="loggedIn">
            <router-link class="btn btn-primary" to="/dashboard">进入应用</router-link>
          </template>
          <template v-else>
            <router-link class="btn btn-ghost" to="/login">登录</router-link>
            <router-link class="btn btn-primary" to="/login?register=1">免费开始</router-link>
          </template>
        </div>
      </div>
    </header>

    <!-- 首屏：文案在上、首页截图整幅在下。
         仪表盘信息密度高，压进半栏就只剩纹理、字全看不清，所以不做左文右图。
         截图从首屏底部露出一截，正好把人往下带。 -->
    <section id="top" class="hero">
      <div class="wrap">
        <div class="hero-copy">
          <p class="eyebrow">A 股全市场 · 结构因子选股 · 历史回放验证</p>
          <h1>每一次选股<br />都经得起<span class="hl">历史验证</span></h1>
          <p class="lede">
            线上选股与历史回放调用同一个评分函数。页面上看到的每一分，都能拉回历史原样复算。
          </p>
          <div class="hero-cta">
            <router-link class="btn btn-primary btn-lg" :to="loggedIn ? '/dashboard' : '/login?register=1'">
              {{ loggedIn ? '进入应用' : '免费开始' }}
            </router-link>
            <a class="btn btn-ghost btn-lg" href="#features">看看产品</a>
          </div>
          <p class="hero-note">
            量化研究工具，不具备证券投资咨询资质。页面上的一切都是算法对公开数据的处理结果，
            <strong>不构成投资建议</strong>，不替你做买卖决定，也不承诺收益。
          </p>
        </div>

        <figure class="hero-art">
          <img
            src="/shots/f-overview.png"
            alt="AStockPick 盘面总览：当日指数行情与各板块最新结果一屏汇总" fetchpriority="high"
          />
          <figcaption>登录后的第一页 · 盘面总览 · 当日真实行情</figcaption>
        </figure>
      </div>
    </section>

    <!-- 六大板块：按 App 侧边栏顺序（盘面总览为第一页）逐块配真实截图。
         截图按 Allen 2026-08-18 指示不做个股打码；含内部阈值说明的区域仍然裁掉 ——
         名单是注册就能看到的，选股口径不是。 -->
    <section id="features" class="band" data-reveal>
      <div class="wrap">
        <h2 class="sec-title">从第一页开始，一页一页看</h2>
        <p class="sec-sub">
          顺序就是登录后的菜单顺序。每张都是产品真实界面的直接截图，不是示意图、也没有美化数据。
        </p>

        <article v-for="(p, i) in pillars" :key="p.name" class="pillar">
          <div class="pillar-head">
            <span class="pillar-no">{{ String(i + 1).padStart(2, '0') }}</span>
            <h3>{{ p.name }}</h3>
            <span class="pillar-metric">{{ p.metric }}</span>
          </div>
          <p class="pillar-lead">{{ p.lead }}</p>
          <figure class="pillar-shot">
            <img :src="p.shot" :alt="`${p.name}功能界面截图`" loading="lazy" />
          </figure>
          <ul class="pillar-points">
            <li v-for="point in p.points" :key="point">{{ point }}</li>
          </ul>
        </article>

        <h3 class="also-head">还有这些</h3>
        <div class="also">
          <div v-for="a in alsoIncluded" :key="a.title" class="also-item">
            <b>{{ a.title }}</b>
            <span>{{ a.body }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- 证据条：整页的主张就是「两个样本都给你看」，所以正负两个数字紧贴首屏，
         访客第一屏之后立刻撞到它。合规声明也放这里，不塞进首屏挤掉 CTA。 -->
    <section class="proof-band" data-reveal>
      <div class="wrap proof-grid">
        <p class="proof-cap">那它到底有没有用 · 同一套评分 · 两个样本</p>
        <div class="proof-nums">
          <div v-for="p in proofs" :key="p.span" class="proof-item">
            <span class="proof-span">{{ p.span }}</span>
            <span class="proof-val" :class="p.tone">{{ p.value }}<em>pp</em></span>
            <span class="proof-note">{{ p.note }}</span>
          </div>
        </div>
        <p class="proof-legal">
          不具备证券投资咨询资质。所有输出为算法对公开数据的自动处理结果，仅供研究参考，
          <strong>不构成投资建议</strong>，不承诺收益。
          <a href="#data">两个数字的完整口径</a>
        </p>
      </div>
    </section>

    <!-- 方法 -->
    <section id="method" class="band" data-reveal>
      <div class="wrap">
        <h2 class="sec-title">可验证的方法</h2>
        <p class="sec-sub">荐股工具遍地都是，能把自己的规则拉回历史复算一遍的没几个。这是本产品唯一的立身之本。</p>
        <ol class="steps">
          <li v-for="step in steps" :key="step.no">
            <span class="step-no">{{ step.no }}</span>
            <h3>{{ step.title }}</h3>
            <p>{{ step.body }}</p>
          </li>
        </ol>
      </div>
    </section>

    <!-- 数据 -->
    <section id="data" class="band band-alt" data-reveal>
      <div class="wrap">
        <h2 class="sec-title">数据全貌，包括不好看的那部分</h2>
        <p class="sec-sub">
          下面每个数字都来自本地日线的 point-in-time 回放，口径、样本量、局限一并列出。
          能被挑窗口挑出来的漂亮数字没有意义，所以我们把长样本的负结果也放在同一页。
        </p>

        <!-- 12 个月同源回放 -->
        <div class="panel">
          <div class="panel-head">
            <h3>12 个月同源回放 · 智能池（结构因子 v3）</h3>
            <span class="tag">130 期 × 2600 样本 · 次日开盘可成交口径 · T+5 对全市场基准</span>
          </div>
          <div class="stats">
            <div v-for="s in replayStats" :key="s.label" class="stat">
              <span class="stat-val" :class="s.tone">{{ s.value }}</span>
              <span class="stat-label">{{ s.label }}</span>
            </div>
          </div>
          <p class="panel-note">
            中位为正意味着收益不只靠少数几只大涨票；入选时已封涨停（页面价买不到）的比例为 0.4%。
            按大盘环境分层：偏暖 +2.11 / 中性 +1.76 / 偏冷 +1.82pp，冷期不衰减。
          </p>
        </div>

        <!-- 长样本负结果 -->
        <div class="panel panel-warn">
          <div class="panel-head">
            <h3>同一套评分拉到 60 个月：超额为负</h3>
            <span class="tag">231 期 · 收盘买入 → T+5 · 基准为全市场均值</span>
          </div>
          <div class="chart">
            <svg :viewBox="`0 0 ${chart.w} ${chart.h}`" role="img" aria-label="分年超额柱状图">
              <line class="axis" :x1="0" :y1="chart.zeroY" :x2="chart.w" :y2="chart.zeroY" />
              <g v-for="bar in bars" :key="bar.year">
                <rect :x="bar.x" :y="bar.y" :width="chart.barW" :height="bar.h"
                      :class="bar.value >= 0 ? 'bar-up' : 'bar-down'" />
                <text class="bar-val" :class="bar.value >= 0 ? 'up' : 'down'"
                      :x="bar.x + chart.barW / 2" :y="bar.labelY">{{ bar.value.toFixed(2) }}</text>
                <text class="bar-year" :x="bar.x + chart.barW / 2" :y="chart.h - 10">{{ bar.year }}</text>
              </g>
            </svg>
            <p class="chart-cap">分年 top-20 平均超额（pp/期）。全样本 <b class="down">−0.62pp/期，t=−2.49</b>。</p>
          </div>
          <p class="panel-note">
            上面那段 12 个月的正超额，恰好落在六年里唯一为正的一段。
            这不推翻「现行权重优于测过的四个变体」（配对检验把市场环境消掉了），
            但足以推翻「这套评分有稳定正超额」。<strong>所以我们不拿它当业绩承诺，你也不该当。</strong>
          </p>
        </div>

        <!-- 局限 -->
        <div class="limits">
          <h3>解读时必须记住的局限</h3>
          <ul>
            <li v-for="item in limits" :key="item">{{ item }}</li>
          </ul>
        </div>
      </div>
    </section>

    <!-- 被否决的方向：整页最不可替代的一节。深色是刻意的，它是这一页的重音，
         不是随机换个底色；页面其余部分保持浅色，只有这里沉下去。 -->
    <section id="rejected" class="reject" data-reveal>
      <div class="wrap">
        <h2 class="reject-title">我们否决掉的东西</h2>
        <p class="reject-sub">
          做加法容易，做减法要有证据。下面每一条都真的测过、真的想接，最后被自己的数据否掉。
          没有一家荐股平台会告诉你这些，因为承认试错就等于承认自己不是神。
        </p>
        <ol class="reject-list">
          <li v-for="r in rejected" :key="r.name">
            <div class="reject-head">
              <h3>{{ r.name }}</h3>
              <span class="reject-date">{{ r.date }}</span>
            </div>
            <p class="reject-body">{{ r.verdict }}</p>
          </li>
        </ol>
        <p class="reject-foot">
          裁决标准在动手之前就定好，不会事后改标准来迁就结果。具体口径属于内部方法，不对外披露。
        </p>
      </div>
    </section>

    <!-- 适合谁 -->
    <section class="band band-alt" data-reveal>
      <div class="wrap fit">
        <div class="fit-col">
          <h3 class="fit-yes">适合</h3>
          <ul>
            <li>接受整池等权、按规则执行的人</li>
            <li>会自己看数据判断、而不是要一个「答案」的人</li>
            <li>能接受连续几个月不跑赢，也不推翻规则的人</li>
          </ul>
        </div>
        <div class="fit-col">
          <h3 class="fit-no">不适合</h3>
          <ul>
            <li>想要「明天涨停的票」的人</li>
            <li>需要收益承诺或保本的人</li>
            <li>只挑一两只重仓、把组合信号当个股信号用的人</li>
          </ul>
        </div>
      </div>
    </section>

    <!-- 定价：整站免费，没有付费档。
         对外只陈述事实（免费、以后也不收费），不解释为什么 —— 把理由摊开反而是在
         提醒读者往那个方向想。标准免责声明在首屏和页脚，那是保护性的，保留。 -->
    <section id="pricing" class="band" data-reveal>
      <div class="wrap">
        <h2 class="sec-title">不收费</h2>
        <div class="free-note">
          <p class="free-lead">全部功能免费。没有付费档，没有「高级版」，<strong>以后也不会有</strong>。</p>
          <p>
            这不是限时活动，也不是先免费后收割。所有选股池、每日名单、历史回放数据和复盘战绩
            都完整开放，不存在任何需要付费才能看到的部分。
          </p>
          <p>
            它是一个公开的个人研究项目：作者自己每天在用，把方法、数据和被否决的结论一并公开。
            没有推销，没有客服追单，也不会有人加你微信喊你充值。
          </p>
          <ul class="free-list">
            <li v-for="item in freeItems" :key="item">{{ item }}</li>
          </ul>
          <router-link class="btn btn-primary btn-lg" :to="loggedIn ? '/dashboard' : '/login?register=1'">
            {{ loggedIn ? '进入应用' : '免费开始' }}
          </router-link>
        </div>
      </div>
    </section>

    <!-- FAQ -->
    <section class="band band-alt" data-reveal>
      <div class="wrap">
        <h2 class="sec-title">常见问题</h2>
        <div class="faq">
          <details v-for="q in faqs" :key="q.q">
            <summary>{{ q.q }}</summary>
            <p>{{ q.a }}</p>
          </details>
        </div>
      </div>
    </section>

    <!-- 页脚 -->
    <footer class="foot">
      <div class="wrap">
        <div class="foot-brand"><BrandLogo :size="26" /><span>AStockPick</span></div>
        <p class="disclaimer">
          风险提示：证券市场有风险，投资可能导致本金损失。AStockPick 是量化研究工具，
          不具备证券投资咨询资质，不提供投资建议、不代客理财、不承诺任何收益。
          页面所有历史数据均为规则回放结果，历史表现不代表未来收益。
        </p>
        <div class="foot-links">
          <router-link to="/legal/terms">用户协议</router-link>
          <router-link to="/legal/privacy">隐私政策</router-link>
          <router-link to="/login">登录 / 注册</router-link>
        </div>
        <p class="copy">© 2026 AStockPick · Allen Ma</p>
      </div>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import BrandLogo from '@/components/Layout/BrandLogo.vue'

// 官网是纯静态展示页：不调任何后端接口，未登录也能完整打开。
// 数字全部来自 docs/superpowers/specs/2026-07-13-replay-validation-report.md
// 与 experiments/ 的实测结论，改数字前请先回到那两处核对口径。
const loggedIn = computed(() => !!localStorage.getItem('auth-token'))

// 入场动效只做一件事：让每一节在进入视口时轻轻抬起。目的是层次，不是表演 ——
// 一个反复声明「不承诺收益」的证券类产品，做成电影级滚动会直接把可信度演没。
// 用 IntersectionObserver 而不是 scroll 监听（后者每帧回调，移动端直接掉帧）。
// prefers-reduced-motion 下整套不挂载，元素保持终态。
const root = ref<HTMLElement | null>(null)
let observer: IntersectionObserver | null = null

onMounted(() => {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (reduce || !root.value) return
  const targets = Array.from(root.value.querySelectorAll<HTMLElement>('[data-reveal]'))
  targets.forEach((el) => el.classList.add('is-pending'))
  observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return
        entry.target.classList.remove('is-pending')
        observer?.unobserve(entry.target)
      })
    },
    { rootMargin: '0px 0px -12% 0px', threshold: 0.08 },
  )
  targets.forEach((el) => observer?.observe(el))
})

onUnmounted(() => {
  observer?.disconnect()
  observer = null
})

const steps = [
  {
    no: '01',
    title: '同源评分',
    body:
      // 只说「同源」这件事本身，不列因子构成和门槛数值 —— 那是选股逻辑，不对外写。
      '多维度结构因子合成打分，并设有流动性下限。关键在于：线上扫描与历史回放调用的是' +
      '同一个评分函数，不存在「线上一套、回测一套」的免责空间。具体因子构成与参数不对外披露。',
  },
  {
    no: '02',
    title: 'Point-in-time 回放',
    body:
      '用本地日线把规则回放 12 个月：每 2 个交易日一期、每期取 top20、T+5 相对全市场基准计超额。' +
      '双口径并列：收盘回测价与次日开盘可成交价，买不到的涨停票如实标注占比。',
  },
  {
    no: '03',
    title: '实盘留痕复盘',
    body:
      '每次扫描自动留痕，按真实行情统计 T+1/T+3/T+5 的胜率、平均收益与超额。' +
      '评分公式一旦变更即换池名，旧公式的战绩不会挂到新公式名下，账不许混着算。',
  },
]

// 被否决的方向。数字全部来自 experiments/README.md 的裁决记录，改动前先回那份文档核对。
// 这一节是整页最不可替代的内容：同行不会公开自己被证伪的东西，因为那意味着承认试错。
// 首屏下方的证据条：与「数据」区同源，改这里必须同时改下面两个面板。
const proofs = [
  { value: '+1.99', tone: 'up', span: '12 个月回放 · 130 期', note: '平均超额 / 期 · 次日开盘可成交口径' },
  { value: '−0.62', tone: 'down', span: '60 个月长样本 · 231 期', note: '平均超额 / 期 · t = −2.49' },
]

const replayStats = [
  { label: '平均超额 / 期', value: '+1.99pp', tone: 'up' },
  { label: '中位超额 / 期', value: '+0.43pp', tone: 'up' },
  { label: '票级超额胜率', value: '51.9%', tone: '' },
  { label: '无重叠子样本 t', value: '3.44', tone: '' },
]

// 2026-08-05 volume_ab 60 个月复核（收盘买入 → T+5，基准全市场均值）
const yearly = [
  { year: 2021, value: -1.45 },
  { year: 2022, value: -1.34 },
  { year: 2023, value: -0.75 },
  { year: 2024, value: -1.28 },
  { year: 2025, value: 0.22 },
  { year: 2026, value: 0.96 },
]

// 零线偏上：最深的一年是 −1.45，负半轴要留得下柱子 + 柱下的数值标签，
// 否则标签会掉到 viewBox 外面被裁掉（正是要展示的那三个负数）。
// 放大到 960 宽是为了让它当整节的主视觉用，而不是塞在面板角落里的缩略图。
const chart = { w: 960, h: 380, barW: 96, zeroY: 132, scale: 82 }

const bars = computed(() =>
  yearly.map((d, i) => {
    const gap = (chart.w - yearly.length * chart.barW) / (yearly.length + 1)
    const x = gap + i * (chart.barW + gap)
    const h = Math.abs(d.value) * chart.scale
    const y = d.value >= 0 ? chart.zeroY - h : chart.zeroY
    return { ...d, x, y, h, labelY: d.value >= 0 ? y - 10 : y + h + 24 }
  }),
)

const limits = [
  '存活偏差：回放宇宙为当前在库股票，期间退市股未包含，偏差方向为高估。',
  '滑点未计：双边交易成本（约 0.13pp/期）已扣，冲击成本与滑点没有。',
  '窗口敏感：会话轴前移 3 周即可让部分结论翻转，任何单次回放数字都不能当承诺。',
  '排除规则用当前股票名称，历史时点的 ST 状态无法还原。',
  '短线波段 / 竞价优选两池依赖盘中数据，日线无法 point-in-time 重建，目前只有实盘留痕，样本不足以下结论。',
]

const rejected = [
  // 只留「测过什么、结论是什么」。具体效应量、t 值、样本口径和预登记标准都撤掉了 ——
  // 那些是方法论本身，公开出去等于把裁决台一并送人。
  { name: '主升浪开启形态', date: '2026-08-13',
    verdict: '民间流传的那套四段式形态。在长样本上检验后不接入排序，它更容易出大牛股，但整体是负期望，而产品吃的是组合平均。' },
  { name: '龙虎榜数据源', date: '2026-08-03',
    verdict: '接过一版，跑出来的正结果经不起复核，判定为多重检验的产物。不接这个源。' },
  { name: '缩量埋伏', date: '2026-08-05',
    verdict: '收盘口径下极好看，换成用户真能买到的入场点就归零。差额全在一段吃不到的跳空里，不接。' },
  { name: '四个权重变体', date: '2026-07-25',
    verdict: '想让排序更「科学」，四个变体在长样本上全部劣于现行权重。现行权重不改。' },
]

// 六个主功能，按 Allen 指定的顺序：一键智选 → 盘中机会雷达 → 集合竞价 → 涨停热点
// → 风险预警 → 盘面总览。
//
// ⚠️ 写这一节的硬规矩：只说「能做什么」，不说「怎么做到的」。
// 具体阈值、因子构成、扫描频率、判定口径一律不写——那些是选股逻辑本身，写上去等于送人抄。
// 之前有一版把 3000 万流动性门槛、成交额前 15%、15 秒扫描、MACD/布林/趋势/动量的因子
// 构成、MA10/MA20 破位口径、100%涨幅/15%回撤/10亿成交额全写上了，已按要求撤掉。
// 截图同理：个股名称与代码一律打码，含内部阈值说明的区域直接裁掉。
const pillars = [
  {
    name: '盘面总览',
    metric: '首页 · 一屏',
    shot: '/shots/f-overview.png',
    lead: '登录后的第一页。进来一眼就知道今天该不该动手，不用逐个点开菜单等加载。',
    points: [
      '最上面直接给出当日大盘环境的结论，先回答「今天值不值得做」',
      '指数、涨跌家数、两市成交额同屏，不用另开行情软件',
      '各板块最新结果摘要 + 直达入口，点进去不用重算',
    ],
  },
  {
    name: '盘中机会雷达',
    metric: '盘中持续',
    shot: '/shots/f-radar.png',
    lead: '盘中持续扫全市场，机会出现的当下就提示，不用守着盘面手动刷新。',
    points: [
      '状态分开标：能进的、只是预警的、已经追不进去的，一眼分得清',
      '每条信号一并标出触发价与结构失效位，而不是只显示一个代码',
      '当天上过榜的信号全部留痕，早盘触发、现在已掉出榜单的也查得到',
    ],
  },
  {
    name: '一键智选',
    metric: '每日 1 份',
    shot: '/shots/f-smart.png',
    lead: '点一次，全市场跑一遍，输出当日的评分结果。条数固定，行情差也不注水凑数。',
    points: [
      '每只票列出结构分、时机确认状态、行业归属与相关价位，不是只丢给你一个代码',
      '入选理由可展开，看得到它凭什么进这份名单',
      '名单当场留痕，事后能查它到底走成了什么样',
    ],
  },
  {
    name: '集合竞价',
    metric: '09:25',
    shot: '/shots/f-auction.png',
    lead: '开盘前就把当日情绪定性，而不是等冲高回落之后再来复盘。',
    points: [
      '竞价撮合一结束就给出当日情绪档位，并说明这个档位的历史含义',
      '全市场高开幅度分布同屏，是普涨还是分化看得出来',
      '热门板块按竞价强度排序，资金在哪个方向合力一目了然',
    ],
  },
  {
    name: '涨停热点',
    metric: '梯队 × 概念',
    shot: '/shots/f-limitup.png',
    lead: '不只告诉你今天几个涨停，而是钱堆在哪一档高度、哪一个方向。',
    points: [
      '涨停、跌停、最高连板、一字板同屏，情绪冷热不用猜',
      '概念板块按涨停家数排布，主线和杂毛分得开',
      '连板梯队逐档展开，看得出是在发酵还是已经见顶',
    ],
  },
  {
    name: '风险预警',
    metric: '四档红绿灯',
    shot: '/shots/f-risk.png',
    lead: '风险分不是一个模糊的形容词。分数怎么来的、每一档历史上对应什么状况，都写出来。',
    points: [
      '市场风险分四档红绿灯，每一档写明它在历史上对应的市场状况',
      '风险分拆成几项分别显示，看得到它为什么给出这个结论',
      '同时给历史依据，不是凭一句话让你减仓',
    ],
  },
  {
    name: '行业热力',
    metric: '全市场 treemap',
    shot: '/shots/f-heatmap.png',
    lead: '面积看市值、颜色看涨跌，一屏看清今天的钱去了哪些行业。',
    points: [
      '全市场行业排成 treemap，强弱和体量同时看得出来，不用逐个板块点开比',
      '点行业下钻成分股，点个股直达深研，不用来回切页面',
      '已归类个股全覆盖，未归类的单独标出，不混进统计',
    ],
  },
  {
    name: '个股深研',
    metric: '输入代码即可',
    shot: '/shots/f-stock.png',
    lead: '手里已经有票，想知道它现在是什么状态，直接查，不用先加自选。',
    points: [
      '给出综合结论和分项打分，而不是一句「看好」或「看空」',
      '带均线的走势图，关键位置直接标在图上',
      '财务速览、市场表现、近期相关新闻同屏，不用另开三个网站',
    ],
  },
  {
    name: '选股复盘',
    metric: 'T+1 / T+3 / T+5',
    shot: '/shots/f-review.png',
    lead: '每次扫描自动留痕，事后按真实行情算账。难看的数字照样挂着。',
    points: [
      '各池分别统计 T+1/T+3/T+5 的胜率、平均收益与相对大盘的超额',
      '留痕明细逐条可查：哪天推了什么、后来怎么走，全部对得上',
      '评分公式一旦变更即换池名，旧公式的战绩不会挂到新公式名下，账不许混着算',
    ],
  },
]
// 其余功能：这些是支撑，不占主版面，但要让人知道产品不止那六块。
const alsoIncluded = [
  { title: '历史回放验证', body: '把选股规则回放 12 个月，输出月度超额、累计曲线与分环境表现。' },
  { title: '全市场形态扫描', body: '并行标注金叉、突破、缩量等形态，自动叠加停牌/ST 与预亏预减排除。' },
  { title: '我的自选股', body: '收藏个股，实时跟踪涨跌与触发的预警。' },
]

// 整站免费，不再有套餐对比。只列「你能拿到什么」。
const freeItems = [
  '全部选股池与每日名单，条数不因免费而缩水',
  '盘中机会雷达 / 集合竞价 / 涨停热点 / 风险预警',
  '行业热力图 / 个股深研 / 选股复盘 / 自选股',
  '历史回放数据与全部复盘战绩，含为负的那部分',
  '不限次数，没有任何需要付费才能看到的部分',
]

const faqs = [
  {
    q: '完全免费，会不会以后再收费？',
    a:
      '不会。这是一个作者自己每天在用的研究项目，公开出来顺带给同样需要的人用，'
      + '本身没有靠它赚钱的打算，所以也不存在「先养用户再收割」这条路径。'
      + '所有功能不限次数，名单、回放数据和复盘战绩全部完整开放。',
  },
  {
    q: '这是荐股服务吗？',
    a:
      '不是。本产品不具备证券投资咨询资质，输出的是算法对公开数据的处理结果：评分、信号与统计，' +
      '不构成投资建议，也不提供代客理财。买卖决策与后果由你自己承担。',
  },
  {
    q: '为什么胜率只有 51.9%，中位数还这么小？',
    a:
      '因为真实情况就是这样。A 股短线信号的单票胜率天然接近抛硬币，超额主要来自组合层面。' +
      '把胜率标到 80% 的产品，要么在挑窗口，要么没做过 point-in-time 回放。我们宁可给你一个难看但可复算的数字。',
  },
  {
    q: '为什么要整池买而不是挑一两只？',
    a:
      '回放数据显示收益结构是「组合有效、单票分布很宽」：p10 与 p90 相差超过 20 个点。' +
      '挑一两只等于把组合信号当个股信号用，大概率吃到的是分布的左半边。产品因此提供「整池等权加入组合」的一键动作。',
  },
  {
    q: '数据从哪来，会不会实时？',
    a:
      '日线以腾讯行情为主、akshare 兜底，本地全市场落库并增量更新；盘中实时行情用于停牌/ST 排除与涨跌展示。' +
      '数据来自公开渠道，不保证实时性、准确性与完整性。',
  },
]
</script>

<style scoped>
/* 数据编辑部式版面：纸白底、墨黑字、等宽数字。
   刻意不用 Element Plus 组件——落地页与应用内是两套视觉语言，混用会两头不像。

   ── 唯一强调色 ──
   强调色取自产品自己的徽标（红渐变 #f2404a → #c2101f），全页只此一色，不再出现第二个
   彩色系。原来定义了一个藏青 --accent 却几乎没用，等于没有强调色，页面才会读起来是黑白的。
   这里有个便宜可占：A 股口径「红涨绿跌」的红，和品牌红是同一族，所以强调色与语义色天然统一，
   不需要为了「设计感」再引入一个跟金融语义打架的颜色。

   ── 圆角 ──
   全页统一 12px（--r），按钮同值。不混用多套圆角。

   字体保持系统栈：中文正文自托管 webfont 动辄几 MB，落地页首屏加载付不起这个代价，
   而 PingFang / 微软雅黑 本身就是中文屏显最好的选择之一。 */
.site {
  --ink: #16181d;
  --ink-2: #4a5058;
  /* #878e98 在纸白上只有 3.09:1，而它承载的是合规声明和统计标签这类必须读清的文字，
     不是纯装饰。压到 4.9:1。 */
  --ink-3: #666d78;
  --paper: #ffffff;
  --paper-2: #f8f7f4;
  --line: #e3e1dc;
  --line-soft: #eeece7;

  --brand: #c2101f;
  --brand-hi: #f2404a;
  --brand-ink: #8d0b16;
  --brand-wash: #fdf3f3;
  --brand-line: #f3d3d5;

  --up: #c2101f;
  --down: #14a44d;
  --down-wash: #eefaf2;
  --surface: #ffffff;
  --nav-bg: rgba(255, 255, 255, .92);
  /* 按钮底色两个模式共用一套深红：徽标那条亮渐变（#f2404a）配白字只有 3.43:1，
     做图形没问题，做按钮文字底色不够。按钮自带背景，本来也不需要跟着页面模式变。 */
  --btn-a: #d81e2c;
  --btn-b: #a80c18;
  --r: 12px;
  /* 等宽栈必须带中文回退：ui-monospace/Consolas 都没有汉字，混排时中文会掉进宋体，
     成为全页唯一的衬线字（「见站内会员页」「130 期 × 2600 样本」都中招过）。 */
  --mono: ui-monospace, Consolas, 'PingFang SC', 'Microsoft YaHei', monospace;

  background: var(--paper);
  color: var(--ink);
  font-family: system-ui, -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  line-height: 1.75;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1120px; margin: 0 auto; padding: 0 24px; }


/* 入场：只做 8px 抬起 + 透明度。JS 只在 prefers-reduced-motion 未开启时挂载，
   所以这里不需要再写一份 media 兜底：降级路径是「类根本不会被加上」。 */
[data-reveal] { transition: opacity .5s cubic-bezier(.16,1,.3,1), transform .5s cubic-bezier(.16,1,.3,1); }
[data-reveal].is-pending { opacity: 0; transform: translateY(8px); }

/* 顶栏 */
.nav {
  position: sticky; top: 0; z-index: 10;
  background: var(--nav-bg);
  backdrop-filter: saturate(180%) blur(8px);
  border-bottom: 1px solid var(--line);
}
.nav-inner { display: flex; align-items: center; gap: 32px; height: 68px; }
.brand { display: flex; align-items: center; gap: 10px; text-decoration: none; color: var(--ink); }
.brand-text { font-weight: 700; letter-spacing: -0.2px; line-height: 1.15; }
.brand-text em { display: block; font-style: normal; font-size: 11px; font-weight: 400; color: var(--ink-3); letter-spacing: 2px; }
.links { display: flex; gap: 26px; margin-left: auto; }
.links a {
  color: var(--ink-2); text-decoration: none; font-size: 14px;
  padding-bottom: 2px; border-bottom: 1.5px solid transparent; transition: .16s;
}
.links a:hover { color: var(--brand-ink); border-bottom-color: var(--brand-ink); }
.nav-cta { display: flex; gap: 10px; }

/* 按钮：主按钮就是品牌本身（同一条红渐变），不是一个黑块。 */
.btn {
  display: inline-block; padding: 9px 18px; border-radius: var(--r); font-size: 14px;
  text-decoration: none; border: 1px solid transparent; white-space: nowrap;
  transition: transform .12s, box-shadow .18s, background .18s, border-color .18s;
}
.btn-primary {
  background: linear-gradient(135deg, var(--btn-a), var(--btn-b));
  color: #fff; box-shadow: 0 1px 2px rgba(194, 16, 31, .28);
}
.btn-primary:hover { box-shadow: 0 6px 18px rgba(194, 16, 31, .3); }
.btn-primary:active { transform: translateY(1px); box-shadow: 0 1px 2px rgba(194, 16, 31, .28); }
.btn-ghost { border-color: var(--line); color: var(--ink); background: var(--surface); }
.btn-ghost:hover { border-color: var(--brand-ink); color: var(--brand-ink); }
.btn-ghost:active { transform: translateY(1px); }
.btn-lg { padding: 13px 28px; font-size: 15px; }

/* 首屏：左文右图的非对称分栏。背景一层极淡的品牌色晕，让白底不再是死白。 */
.hero {
  padding: 76px 0 72px; position: relative; overflow: hidden;
  background:
    radial-gradient(58% 70% at 88% 8%, rgba(242, 64, 74, .10), transparent 62%),
    radial-gradient(40% 55% at 2% 96%, rgba(20, 164, 77, .07), transparent 60%),
    var(--paper);
}
.hero-copy { max-width: 760px; }
.eyebrow {
  font-size: 12.5px; letter-spacing: 1.4px; color: var(--brand-ink); margin: 0 0 18px; font-weight: 600;
}
.hero h1 {
  font-size: clamp(38px, 5.4vw, 58px); line-height: 1.14; letter-spacing: -1.4px;
  font-weight: 800; margin: 0 0 20px;
}
/* 标题重音用同族的渐变，不引第二个颜色，也不整句都上色。 */
.hl {
  background: linear-gradient(120deg, var(--brand-hi), var(--brand));
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.lede { font-size: 18px; color: var(--ink-2); max-width: 640px; margin: 0 0 30px; }
.hero-cta { display: flex; gap: 12px; flex-wrap: wrap; }
.hero-note {
  margin: 22px 0 0; font-size: 13px; color: var(--ink-3);
  border-left: 2px solid var(--brand-line); padding-left: 12px; max-width: 640px;
}
.hero-note strong { color: var(--ink-2); }

.hero-art { margin: 44px 0 0; }
.hero-art img {
  display: block; width: 100%; height: auto; border-radius: var(--r);
  border: 1px solid var(--line);
  box-shadow: 0 24px 60px -28px rgba(22, 24, 29, .42), 0 2px 8px rgba(22, 24, 29, .06);
}
.hero-art figcaption { margin-top: 12px; font-size: 12.5px; color: var(--ink-3); padding-bottom: 56px; }

/* 证据条 */
.proof-band { background: var(--paper-2); border-block: 1px solid var(--line); padding: 34px 0; }
.proof-grid { display: grid; gap: 18px; }
.proof-cap {
  font: 600 11px/1 var(--mono);
  letter-spacing: 1.6px; color: var(--ink-3); margin: 0;
}
.proof-nums { display: flex; flex-wrap: wrap; gap: 14px 56px; align-items: flex-end; }
.proof-span { display: block; font-size: 12.5px; font-weight: 600; color: var(--ink-2); }
.proof-val {
  display: block; margin: 2px 0 2px; white-space: nowrap;
  font: 700 38px/1.1 var(--mono); letter-spacing: -1.5px;
}
.proof-val em { font-size: 15px; font-style: normal; margin-left: 2px; letter-spacing: 0; }
.proof-note { display: block; font-size: 12px; line-height: 1.5; color: var(--ink-3); }
.proof-legal { font-size: 12.5px; color: var(--ink-3); margin: 0; max-width: 860px; }
.proof-legal strong { color: var(--ink-2); }
.proof-legal a { color: var(--brand-ink); text-decoration: none; border-bottom: 1px solid var(--brand-line); }
.proof-legal a:hover { border-bottom-color: var(--brand-ink); }

/* 区块 */
.band { padding: 80px 0; }
.band-alt { background: var(--paper-2); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.sec-title {
  font-size: clamp(26px, 3.4vw, 36px); letter-spacing: -0.8px; margin: 0 0 12px; font-weight: 750;
  padding-left: 16px; border-left: 4px solid var(--brand);
}
.sec-sub { color: var(--ink-2); max-width: 720px; margin: 0 0 44px; padding-left: 20px; }

/* 方法三步：序号是这一节唯一的装饰，给它品牌色和体量。 */
.steps { list-style: none; padding: 0; margin: 0; display: grid; gap: 28px; grid-template-columns: repeat(3, 1fr); }
.steps li { border-top: 2px solid var(--brand); padding-top: 16px; }
.step-no {
  font: 800 30px/1 var(--mono); letter-spacing: -1px;
  color: var(--brand-ink); opacity: .3;
}
.steps h3 { font-size: 18px; margin: 6px 0 8px; }
.steps p { color: var(--ink-2); font-size: 14.5px; margin: 0; }

/* 数据面板：正结果和负结果各自带色，一眼看出这页在同时展示两种结论。 */
.panel {
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r);
  padding: 26px 28px; margin-bottom: 22px;
  border-top: 3px solid var(--up); box-shadow: 0 1px 3px rgba(22, 24, 29, .04);
}
.panel-warn { border-top-color: var(--down); }
.panel-head { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; margin-bottom: 20px; }
.panel-head h3 { font-size: 18px; margin: 0; }
.tag { font: 400 12px/1.6 var(--mono); color: var(--ink-3); }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
.stat { padding: 14px 16px; border-radius: var(--r); background: var(--paper-2); }
.stat-val { display: block; font: 700 27px/1.2 var(--mono); letter-spacing: -0.5px; }
.stat-label { font-size: 12.5px; color: var(--ink-3); }
.panel-note { font-size: 13.5px; color: var(--ink-2); margin: 20px 0 0; padding-top: 16px; border-top: 1px dashed var(--line); }
.panel-note strong { color: var(--ink); }

/* 分年柱图：这一节的主视觉，给它整块宽度和体量。 */
.chart { margin: 8px 0 4px; }
.chart svg { width: 100%; height: auto; display: block; overflow: visible; }
.axis { stroke: var(--ink-3); stroke-width: 1; stroke-dasharray: 3 3; opacity: .5; }
.bar-up { fill: var(--up); }
.bar-down { fill: var(--down); }
.bar-val { font: 700 21px var(--mono); text-anchor: middle; letter-spacing: -0.5px; }
.bar-year { font: 600 16px system-ui, sans-serif; fill: var(--ink-3); text-anchor: middle; }
.chart-cap { font-size: 13.5px; color: var(--ink-2); text-align: center; margin: 14px 0 0; }
.up { color: var(--up); fill: var(--up); }
.down { color: var(--down); fill: var(--down); }

/* 被否决的方向：全页的重音块。用品牌色浅底 + 左侧粗描边把它顶出来，
   不靠黑底 —— 整页保持浅色，不做深浅反转。 */
.reject {
  background:
    radial-gradient(46% 60% at 88% 0%, rgba(242, 64, 74, .07), transparent 62%),
    var(--brand-wash);
  border-block: 1px solid var(--brand-line);
  padding: 84px 0;
}
.reject-title {
  font-size: clamp(28px, 4vw, 42px); letter-spacing: -1px; font-weight: 800; margin: 0 0 14px;
  padding-left: 16px; border-left: 4px solid var(--brand);
}
.reject-sub { color: var(--ink-2); max-width: 720px; margin: 0 0 40px; padding-left: 20px; font-size: 15px; }
.reject-list { list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
.reject-list li {
  border: 1px solid var(--brand-line); border-radius: var(--r); padding: 24px 26px;
  background: var(--surface); transition: border-color .18s, box-shadow .18s, transform .18s;
}
.reject-list li:hover {
  border-color: var(--brand); transform: translateY(-2px);
  box-shadow: 0 14px 32px -22px rgba(194, 16, 31, .5);
}
.reject-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.reject-head h3 { font-size: 17px; margin: 0; color: var(--brand-ink); }
.reject-date { font: 400 12px var(--mono); color: var(--ink-3); }
.reject-body { font-size: 13.5px; line-height: 1.85; color: var(--ink-2); margin: 0; }
.reject-foot {
  margin: 32px 0 0; font-size: 13px; color: var(--ink-2);
  border-left: 3px solid var(--brand); padding-left: 14px; max-width: 760px;
}

/* 真实界面截图 */
.shot { margin: 0 0 34px; }
.shot:last-child { margin-bottom: 0; }
.shot img {
  display: block; width: 100%; height: auto;
  border: 1px solid var(--line); border-radius: 10px; background: #fff;
}
.shot figcaption { font-size: 13.5px; color: var(--ink-2); margin-top: 12px; }
.shot figcaption b { color: var(--ink); font-weight: 600; }

/* 局限 */
.limits { border: 1px solid var(--brand-line); border-radius: var(--r); padding: 22px 28px; background: var(--brand-wash); }
.limits h3 { font-size: 15px; margin: 0 0 12px; color: var(--brand-ink); }
.limits ul { margin: 0; padding-left: 18px; color: var(--ink-2); font-size: 13.5px; }
.limits li { margin-bottom: 6px; }

/* 六大板块：每块是「标题条 → 定位句 → 整幅截图 → 要点」的纵向结构。
   刻意不做左图右文的交替版式 —— 六块连着交替会读成模板，而且截图是宽幅的，
   压到半栏里字就看不清了。 */
.pillar {
  border-top: 2px solid var(--brand); padding: 26px 0 40px;
}
.pillar + .pillar { margin-top: 8px; }
.pillar-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.pillar-no { font: 800 26px/1 var(--mono); color: var(--brand); opacity: .3; letter-spacing: -1px; }
.pillar-head h3 { font-size: 22px; margin: 0; letter-spacing: -0.4px; }
.pillar-metric {
  margin-left: auto; font: 600 11.5px var(--mono); color: var(--brand-ink);
  background: var(--brand-wash); border: 1px solid var(--brand-line);
  border-radius: 999px; padding: 3px 12px; white-space: nowrap;
}
.pillar-lead { font-size: 15px; color: var(--ink); margin: 10px 0 20px; max-width: 780px; }
.pillar-shot { margin: 0 0 18px; }
.pillar-shot img {
  display: block; width: 100%; height: auto; border-radius: var(--r);
  border: 1px solid var(--line); background: #fff;
  box-shadow: 0 18px 46px -30px rgba(22, 24, 29, .5);
}
.pillar-points { margin: 0; padding: 0; list-style: none; display: grid; gap: 0; }
.pillar-points li {
  font-size: 13.5px; line-height: 1.75; color: var(--ink-2);
  padding: 8px 0 8px 18px; position: relative; border-top: 1px solid var(--line-soft);
}
.pillar-points li::before {
  content: ''; position: absolute; left: 2px; top: 16px;
  width: 5px; height: 5px; border-radius: 50%; background: var(--brand); opacity: .6;
}

/* 还有这些：次要功能用两列文本行，与上面的卡片是两种版式，不重复。 */
.also-head { font-size: 15px; margin: 44px 0 16px; color: var(--ink-3); font-weight: 600; }
.also { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0 40px; border-top: 1px solid var(--line); }
.also-item { display: flex; gap: 12px; padding: 13px 0; border-bottom: 1px solid var(--line-soft); font-size: 13.5px; }
.also-item b { flex: 0 0 96px; font-weight: 600; }
.also-item span { color: var(--ink-2); }

/* 适合谁 */
.fit { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
.fit-col { padding: 24px 26px; border-radius: var(--r); background: var(--surface); border: 1px solid var(--line); }
.fit-col:first-child { background: var(--down-wash); border-color: #cdeada; }
.fit-col ul { margin: 0; padding-left: 18px; color: var(--ink-2); }
.fit-col li { margin-bottom: 10px; }
.fit-yes, .fit-no { font-size: 18px; margin: 0 0 14px; padding-bottom: 10px; border-bottom: 2px solid; }
.fit-yes { border-color: var(--down); color: #0b7c3a; }
.fit-no { border-color: var(--line); color: var(--ink-3); }

/* 定价：没有套餐对比，就一段声明 + 一份清单。 */
.free-note {
  border: 1px solid var(--brand-line); border-radius: var(--r);
  background: linear-gradient(150deg, var(--brand-wash), var(--surface) 58%);
  padding: 30px 32px; max-width: 800px;
}
.free-lead { font-size: 19px; font-weight: 700; margin: 0 0 14px; letter-spacing: -0.3px; }
.free-note p { color: var(--ink-2); margin: 0 0 12px; font-size: 14.5px; }
.free-note p strong { color: var(--brand-ink); }
.free-list { list-style: none; padding: 0; margin: 20px 0 24px; border-top: 1px solid var(--brand-line); }
.free-list li {
  font-size: 14px; color: var(--ink-2); padding: 10px 0 10px 20px;
  position: relative; border-bottom: 1px solid var(--line-soft);
}
.free-list li::before {
  content: ''; position: absolute; left: 3px; top: 19px;
  width: 5px; height: 5px; border-radius: 50%; background: var(--brand); opacity: .6;
}

/* FAQ */
.faq { max-width: 820px; border-top: 1px solid var(--line); }
.faq details { border-bottom: 1px solid var(--line); padding: 18px 0; }
.faq summary { cursor: pointer; font-weight: 600; font-size: 15.5px; list-style: none; }
.faq summary::-webkit-details-marker { display: none; }
.faq summary::before { content: '+ '; color: var(--brand-ink); font-weight: 600; }
.faq details[open] summary::before { content: '− '; }
.faq details[open] summary { color: var(--brand-ink); }
.faq p { color: var(--ink-2); font-size: 14.5px; margin: 12px 0 0; }

/* 页脚 */
.foot { background: var(--ink); color: #a8aeb8; padding: 48px 0 40px; font-size: 13px; }
.foot-brand { display: flex; align-items: center; gap: 10px; color: #fff; font-weight: 700; font-size: 16px; margin-bottom: 20px; }
.disclaimer { max-width: 760px; margin: 0 0 24px; line-height: 1.9; }
.foot-links { display: flex; gap: 22px; padding-bottom: 20px; border-bottom: 1px solid #2b2f38; }
.foot-links a { color: #d6dae0; text-decoration: none; }
.foot-links a:hover { color: #fff; }
.copy { margin: 18px 0 0; color: #6d7480; }

@media (max-width: 980px) {
  .pillars, .reject-list { grid-template-columns: 1fr; }
}

@media (max-width: 860px) {
  .links { display: none; }
  .steps, .fit { grid-template-columns: 1fr; }
  .pillars, .reject-list, .also { grid-template-columns: 1fr; }
  .pillar-metric { margin-left: 0; }
  .stats { grid-template-columns: repeat(2, 1fr); row-gap: 14px; }
  .proof-nums { gap: 18px 34px; }
  .proof-val { font-size: 32px; }
  .sec-title { padding-left: 12px; border-left-width: 3px; }
  .sec-sub { padding-left: 15px; }
  /* SVG 文字跟着 viewBox 等比缩，窄屏下 12 用户单位≈7px，得按用户单位加大 */
  .bar-val { font-size: 19px; }
  .bar-year { font-size: 17px; }
  .hero { padding: 56px 0 0; }
  .band { padding: 56px 0; }
  .fit { gap: 32px; }
}
</style>
