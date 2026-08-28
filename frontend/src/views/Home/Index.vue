<template>
  <div class="site" ref="root">
    <!-- 顶栏 -->
    <header class="nav">
      <div class="wrap nav-inner">
        <!-- 用 App 里那个真的品牌徽标，不要另造一个：落地页和产品内必须是同一个牌子。 -->
        <a class="brand" href="#top">
          <BrandLogo :size="24" />
          <span class="brand-text">ASTOCKPICK</span>
        </a>
        <nav class="links">
          <a href="#method">METHOD</a>
          <a href="#audit">AUDIT</a>
          <a href="#result">RESULTS</a>
          <a href="#product">PRODUCT</a>
          <a href="#day">A DAY</a>
          <a href="#rejected">REJECTED</a>
          <a href="#pricing">PRICING</a>
        </nav>
        <div class="nav-cta">
          <template v-if="loggedIn">
            <router-link class="btn btn-primary" to="/dashboard">进入应用</router-link>
          </template>
          <template v-else>
            <router-link class="btn-text" to="/login">登录</router-link>
            <router-link class="btn btn-primary" to="/login?register=1">免费开始</router-link>
          </template>
        </div>
      </div>
    </header>

    <!-- 行情条：静态快照，不调接口。官网未登录也要能整页打开。 -->
    <div class="ticker">
      <div class="wrap ticker-inner">
        <span v-for="t in tickers" :key="t.label" class="ticker-cell">
          {{ t.label }}
          <b :class="t.tone">{{ t.value }}</b>
          <em v-if="t.extra">{{ t.extra }}</em>
        </span>
      </div>
    </div>

    <!-- 首屏 -->
    <section id="top" class="hero">
      <div class="wrap hero-grid">
        <div class="hero-copy">
          <p class="eyebrow">SAME SCORING FUNCTION · LIVE &amp; REPLAY</p>
          <h1>同一个评分函数<br />线上跑一遍<span class="comma">，</span>历史再跑一遍</h1>
          <p class="lede">
            页面上看到的每一分，都能拉回历史原样复算。复算出来不好看的那一部分，也在这一页上。
          </p>
          <div class="hero-cta">
            <router-link class="btn btn-primary btn-lg" :to="loggedIn ? '/dashboard' : '/login?register=1'">
              {{ loggedIn ? '进入应用' : '免费开始' }}
            </router-link>
            <a class="btn btn-ghost btn-lg" href="#method">先看方法</a>
          </div>
          <p class="hero-free">全部功能免费 · 无付费档 · 不限次数</p>
          <p class="hero-note">
            量化研究工具，不具备证券投资咨询资质。页面上的一切都是算法对公开数据的处理结果，
            <strong>不构成投资建议</strong>，不替你做买卖决定，也不承诺收益。
          </p>
        </div>

        <aside class="hero-panel">
          <div class="panel-cap">
            <span class="strong">今日智能选股</span><span class="spacer"></span><span>TOP 20 · 08-27</span>
          </div>
          <div class="pick-head">
            <span>#</span><span>名称 / 代码</span><span class="r">结构分</span><span class="r">T+5</span>
          </div>
          <div v-for="p in heroPicks" :key="p.code" class="pick-row">
            <span class="dim">{{ p.no }}</span>
            <span>{{ p.name }} <span class="dim">{{ p.code }}</span></span>
            <span class="r up strong">{{ p.score }}</span>
            <span class="r" :class="p.ret >= 0 ? 'up' : 'down'">{{ fmt(p.ret) }}</span>
          </div>
          <p class="panel-foot">示意数据 · 真实名单登录后可见 · 留痕后按真实行情算账</p>
        </aside>
      </div>
    </section>

    <!-- 证据条：整页的主张就是「两个样本都给你看」，所以正负两个数字紧贴首屏。 -->
    <section class="evidence" data-reveal>
      <div class="wrap evidence-grid">
        <div v-for="e in proofs" :key="e.span" class="evidence-cell">
          <span class="cap">{{ e.span }}</span>
          <span class="big" :class="e.tone">{{ e.value }}<em>pp</em></span>
          <span class="sub">{{ e.note }}</span>
        </div>
      </div>
    </section>

    <!-- 01 方法 -->
    <section id="method" class="band" data-reveal>
      <div class="wrap">
        <div class="sec-head">
          <span class="sec-no">01 / METHOD</span>
          <h2>可验证的方法</h2>
          <span class="sec-sub">不存在「线上一套、回测一套」的免责空间</span>
        </div>
        <div class="cols-3 ruled">
          <div v-for="s in steps" :key="s.no" class="col">
            <span class="step-no">STEP {{ s.no }}</span>
            <h3>{{ s.title }}</h3>
            <p>{{ s.body }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- 02 七道闸 -->
    <section id="audit" class="band band-alt" data-reveal>
      <div class="wrap">
        <div class="sec-head tight">
          <span class="sec-no">02 / AUDIT</span>
          <h2>一条规则要过七道闸才准进排序</h2>
        </div>
        <p class="sec-lede">
          「看着不错」已经把我们坑过好几次：平均值为正其实只靠少数几只大涨股拉起来、换个时间跨度结论就翻号、
          横竖切十几刀总能切出一个显著。每被坑一次，就把对应的检验固化成一道闸。
          <strong>七道全过才进排序，过不了最多当观察标记。</strong>
        </p>

        <div class="table">
          <div class="tr th">
            <span>#</span><span>闸门</span><span>它拦的是哪个坑</span>
          </div>
          <div v-for="g in gates" :key="g.no" class="tr" :class="{ core: g.core }">
            <span class="gate-no">{{ g.no }}</span>
            <span class="gate-name">
              {{ g.name }}
              <em v-if="g.core">CORE</em>
            </span>
            <span class="gate-body">{{ g.body }}</span>
          </div>
        </div>

        <p class="callout">
          七道闸的阈值写死在文件顶部，<strong>不许在看到结果之后再改</strong> ——
          否则「调到显著为止」是必然结局。具体数值与实现属于内部方法，不对外披露。
        </p>

        <div class="passed">
          <div class="passed-head">
            <span class="cap">PASSED · 至今仅此一条</span>
            <h3>板块中期动量</h3>
          </div>
          <p>
            这把尺子建立以来，唯一一条七道闸全过的规则：持有期越长越强、不靠右尾、多个年份方向一致。它已经进了排序。<br />
            同一轮审计里，产品自己原有的一个板块因子被查出主导项没有 alpha ——
            <strong>已按结果改掉，而不是当没看见。</strong>尺子对内对外是同一把。
          </p>
        </div>
      </div>
    </section>

    <!-- 03 结果 -->
    <section id="result" class="band" data-reveal>
      <div class="wrap">
        <div class="sec-head">
          <span class="sec-no">03 / RESULTS</span>
          <h2>结果，包括不好看的那部分</h2>
          <span class="sec-sub">口径、样本量、局限一并列出</span>
        </div>

        <div class="panel">
          <div class="panel-head">
            <span class="tag up">TABLE 01</span>
            <h3>12 个月同源回放 · 智能池（结构因子 v3）</h3>
            <span class="meta">130 期 × 2600 样本 · 次日开盘可成交口径 · T+5 对全市场基准</span>
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

        <div class="panel">
          <div class="panel-head">
            <span class="tag down">CHART 01</span>
            <h3>同一套评分拉到 60 个月：超额为负</h3>
            <span class="meta">231 期 · 收盘买入 → T+5 · 基准为全市场均值</span>
          </div>
          <div class="chart">
            <svg :viewBox="`0 0 ${chart.w} ${chart.h}`" role="img" aria-label="分年超额柱状图">
              <line class="axis" x1="0" :y1="chart.zeroY" :x2="chart.w" :y2="chart.zeroY" />
              <text class="axis-label" x="0" :y="chart.zeroY - 8">0</text>
              <g v-for="b in bars" :key="b.year">
                <rect :x="b.x" :y="b.y" :width="chart.barW" :height="b.h"
                      :class="b.value >= 0 ? 'bar-up' : 'bar-down'" />
                <text class="bar-val" :class="b.value >= 0 ? 'up' : 'down'"
                      :x="b.x + chart.barW / 2" :y="b.labelY">{{ fmt(b.value) }}</text>
                <text class="bar-year" :x="b.x + chart.barW / 2" :y="chart.h - 38">{{ b.year }}</text>
              </g>
            </svg>
            <p class="chart-cap">
              分年 top-20 平均超额（pp / 期）· 全样本 <b class="down">−0.62pp / 期，t = −2.49</b>
            </p>
          </div>
          <p class="panel-note">
            上面那段 12 个月的正超额，恰好落在六年里唯一为正的一段。这不推翻「现行权重优于测过的四个变体」
            （配对检验把市场环境消掉了），但足以推翻「这套评分有稳定正超额」。
            <strong>所以我们不拿它当业绩承诺，你也不该当。</strong>
          </p>
        </div>

        <div class="caveats">
          <div class="caveats-side">
            <span class="cap up">CAVEATS</span>
            <h3>解读时必须<br />记住的局限</h3>
          </div>
          <div class="caveats-list">
            <div v-for="(l, i) in limits" :key="l" class="caveat">
              <span class="rn">{{ roman[i] }}</span><span>{{ l }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 04 产品：所有板块版面由 DOM + SVG 重绘，不用位图截图，理由见 style 顶部注释。 -->
    <section id="product" class="band" data-reveal>
      <div class="wrap">
        <div class="sec-head tight">
          <span class="sec-no">04 / PRODUCT</span>
          <h2>这套规则做成了什么</h2>
          <span class="sec-sub">十个板块，顺序就是登录后的菜单顺序</span>
        </div>
        <p class="sec-lede muted rule-left">
          下列版面均按产品真实界面重绘为矢量示意图，结构、字段与列都与产品一致；
          数值为 2026-08-19 的真实快照，含为负的那部分。K 线为示意走势，不代表任何标的。
        </p>

        <div class="windows">
          <!-- 盘面总览 -->
          <div class="win">
            <div class="win-head"><b>盘面总览</b><span class="spacer"></span><span>登录后的第一页 · 一屏</span></div>
            <div class="win-body media-left">
              <div class="media">
                <div class="screen">
                  <div class="ov-top">
                    <span class="k">赚钱效应</span>
                    <span class="v">中性</span>
                    <span class="m">温度 40.9</span>
                    <span class="m">个股中位 <b class="down">−0.29%</b></span>
                    <span class="m">上涨家数占比 <b>23%</b></span>
                    <span class="m down strong">普跌</span>
                    <span class="spacer"></span>
                    <span class="m dim">示意 · 2026-08-19 09:20</span>
                  </div>
                  <div class="ov-risk">
                    <span class="k">全市场风险仪表</span>
                    <span class="v">44.6</span>
                    <span class="warn strong">警惕</span>
                    <span class="tiers">
                      <i class="t-safe"></i><i class="t-warn"></i><i class="t-danger"></i><i class="t-crit"></i>
                    </span>
                    <span class="say">强弱分化明显，普涨不成立。</span>
                  </div>
                  <div class="ov-grid">
                    <div class="mini">
                      <div class="mini-head"><b>智能选股</b><span class="spacer"></span><span>进入 →</span></div>
                      <span v-for="p in heroPicks.slice(0, 3)" :key="p.code" class="kvline">
                        <span class="dim">{{ p.no }} {{ p.name }}</span><span class="up strong">{{ p.score }} 分</span>
                      </span>
                    </div>
                    <div class="mini">
                      <div class="mini-head"><b>涨停热点</b><span class="spacer"></span><span>进入 →</span></div>
                      <div class="tri">
                        <span v-for="s in ovLimit" :key="s.label"><b :class="s.tone">{{ s.value }}</b><i>{{ s.label }}</i></span>
                      </div>
                    </div>
                    <div class="mini">
                      <div class="mini-head"><b>风险预警</b><span class="spacer"></span><span>进入 →</span></div>
                      <div class="tri">
                        <span v-for="s in ovRisk" :key="s.label"><b :class="s.tone">{{ s.value }}</b><i>{{ s.label }}</i></span>
                      </div>
                    </div>
                    <div class="mini">
                      <div class="mini-head"><b>行业热力</b><span class="spacer"></span><span>进入 →</span></div>
                      <span v-for="h in ovHeat" :key="h.name" class="barline">
                        <span class="bl-name">{{ h.name }}</span>
                        <span class="bl-track"><i :style="{ width: h.w + '%' }" :class="h.tone"></i></span>
                        <span class="bl-val" :class="h.tone">{{ h.value }}</span>
                      </span>
                    </div>
                    <div class="mini">
                      <div class="mini-head"><b>选股复盘</b><span class="spacer"></span><span>进入 →</span></div>
                      <div class="mini-tbl">
                        <span class="dim">池</span><span class="dim r">T+1</span><span class="dim r">T+3</span><span class="dim r">T+5</span>
                        <template v-for="p in ovReview" :key="p.name">
                          <span>{{ p.name }}</span>
                          <span v-for="(c, i) in p.cells" :key="i" class="r strong" :class="c.tone">{{ c.v }}</span>
                        </template>
                      </div>
                    </div>
                    <div class="mini">
                      <div class="mini-head"><b>集合竞价</b><span class="spacer"></span><span>进入 →</span></div>
                      <div class="tri">
                        <span v-for="s in ovAuction" :key="s.label"><b>{{ s.value }}</b><i>{{ s.label }}</i></span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div class="copy">
                <h3>今天该不该动手</h3>
                <p>进来一眼就知道，不用逐个点开菜单等加载。</p>
                <ul class="ticks">
                  <li>最上面直接给出当日大盘环境的结论</li>
                  <li>指数、涨跌家数、两市成交额同屏</li>
                  <li>各板块最新结果摘要 + 直达入口，点进去不用重算</li>
                </ul>
              </div>
            </div>
          </div>

          <!-- 智能选股 -->
          <div class="win">
            <div class="win-head"><b>智能选股</b><span class="spacer"></span><span>每日 1 份 · 条数固定</span></div>
            <div class="win-body media-right">
              <div class="copy">
                <h3>点一次，全市场跑一遍</h3>
                <p>行情差也不注水凑数。</p>
                <ul class="ticks">
                  <li>每只票列出结构分、时机确认状态、行业与相关价位</li>
                  <li>入选理由可展开，看得到它凭什么进这份名单</li>
                  <li>名单当场留痕，事后能查它到底走成了什么样</li>
                </ul>
              </div>
              <div class="media">
                <div class="screen">
                  <div class="sm-bar">
                    <b>一键智能推荐股票池</b>
                    <span class="chip">全市场动量优选</span>
                    <span class="chip">短线波段 1-3 日</span>
                    <span class="chip">推荐上限 20</span>
                    <span class="spacer"></span>
                    <span class="chip solid">一键智能推荐</span>
                  </div>
                  <div class="sm-sub"><b>推荐 20 只</b><span class="dim">已留痕 · 2026-08-19 09:20 · 示意</span></div>
                  <div class="sm-head">
                    <span v-for="h in smartCols" :key="h">{{ h }}</span>
                  </div>
                  <div v-for="r in smartRows" :key="r.code" class="sm-row">
                    <span class="rank">{{ r.rank }}</span>
                    <span class="mono">{{ r.struct }}</span>
                    <span class="timing" :class="r.confirmed ? 'ok' : 'wait'">{{ r.timing }}</span>
                    <span class="mono dim">{{ r.code }}</span>
                    <span>{{ r.name }}</span>
                    <span class="dim">{{ r.industry }}</span>
                    <span class="mono r">{{ r.price }}</span>
                    <span class="mono r up strong">{{ r.chg }}</span>
                    <span class="ref" v-html="r.ref"></span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 选股复盘 -->
          <div class="win">
            <div class="win-head"><b>选股复盘</b><span class="spacer"></span><span>T+1 / T+3 / T+5</span></div>
            <div class="win-body media-left">
              <div class="media">
                <div class="screen">
                  <div class="rv-bar">
                    <b>选股复盘</b>
                    <span class="say">每次扫描自动留痕，按真实行情统计各池 T+1 / T+3 / T+5 —— 数据说话，不承诺胜率。</span>
                    <span class="spacer"></span>
                    <span class="chip down">数据新鲜 · 覆盖 5525/5525</span>
                  </div>
                  <div class="rv-grid">
                    <div v-for="p in reviewPools" :key="p.name" class="rv-pool">
                      <div class="rv-pool-head"><b>{{ p.name }}</b><span class="spacer"></span><span class="dim">{{ p.marks }}</span></div>
                      <div class="rv-cells">
                        <span v-for="c in p.cells" :key="c.h" class="rv-cell">
                          <b :class="c.tone">{{ c.win }}</b>
                          <i>{{ c.h }}<br />均 {{ c.avg }}<br />超额 {{ c.ex }}<br />{{ c.n }} 样本</i>
                        </span>
                      </div>
                    </div>
                  </div>
                  <p class="screen-foot">另有三个对照池同屏，只用于同轴比较，不进产品名单。数值为 2026-08 留痕快照，不是承诺。</p>
                </div>
              </div>
              <div class="copy">
                <h3>事后按真实行情算账</h3>
                <p>难看的数字照样挂着。</p>
                <ul class="ticks">
                  <li>各池分别统计胜率、平均收益与相对大盘的超额</li>
                  <li>留痕明细逐条可查：哪天推了什么、后来怎么走</li>
                  <li>评分公式一变即换池名，账不许混着算</li>
                </ul>
              </div>
            </div>
          </div>
        </div>

        <!-- 其余板块 -->
        <div class="gallery-head">
          <span class="cap">其余板块 · 与登录后的菜单一一对应</span>
          <span class="spacer"></span>
          <span class="cap dim">同为按真实版面重绘</span>
        </div>
        <div class="gallery">
          <!-- 集合竞价 -->
          <div class="card">
            <div class="win-head"><b>集合竞价</b><span class="spacer"></span><span>09:25 撮合结束</span></div>
            <div class="card-media">
              <div class="screen">
                <div class="au-top">
                  <span class="k">竞价情绪</span><span class="v down">弱</span>
                  <span class="spacer"></span><span class="m dim">示意 · 2026-08-19 09:25</span>
                </div>
                <div class="au-kv">
                  <span v-for="k in auctionKv" :key="k.label">
                    <span class="dim">{{ k.label }}</span>
                    <span v-html="k.value"></span>
                  </span>
                </div>
                <div class="au-dist">
                  <span class="dim">高开幅度分布</span>
                  <span class="dist">
                    <i v-for="(d, i) in openDist" :key="i" :style="{ flexGrow: d.n, opacity: d.o }" :class="d.tone"></i>
                  </span>
                </div>
                <p class="screen-say">竞价普遍低开，当日情绪偏弱，控制仓位、以防守为主。</p>
              </div>
            </div>
            <div class="card-copy">
              <h3>开盘前就把当日定性</h3>
              <ul class="ticks">
                <li>撮合一结束就给出情绪档位，并说明这个档位历史上对应什么状况</li>
                <li>全市场高开幅度分布同屏，是普涨还是分化看得出来</li>
                <li>热门板块按竞价强度排序，资金在哪个方向合力一目了然</li>
              </ul>
            </div>
          </div>

          <!-- 行业热力 -->
          <div class="card">
            <div class="win-head"><b>行业热力</b><span class="spacer"></span><span>全市场 treemap</span></div>
            <div class="card-media">
              <div class="screen">
                <div class="tm-top">
                  <b>行业热力图</b><span class="dim">面积 = 市值 · 颜色 = 涨跌幅</span>
                  <span class="spacer"></span><span class="dim">示意</span>
                </div>
                <div class="treemap">
                  <div v-for="(row, ri) in treemap" :key="ri" class="tm-row" :style="{ height: row.h + 'px' }">
                    <span v-for="c in row.cells" :key="c.name" class="tm-cell"
                          :style="{ flexGrow: c.w, background: c.bg }">
                      <b>{{ c.name }}</b><i>{{ c.value }}</i>
                    </span>
                  </div>
                </div>
              </div>
            </div>
            <div class="card-copy">
              <h3>今天的钱去了哪些行业</h3>
              <ul class="ticks">
                <li>面积看市值、颜色看涨跌，强弱和体量同时看得出来</li>
                <li>点行业下钻成分股，点个股直达深研，不用来回切页面</li>
                <li>已归类个股全覆盖，未归类的单独标出，不混进统计</li>
              </ul>
            </div>
          </div>

          <!-- 涨停热点 -->
          <div class="card">
            <div class="win-head"><b>涨停热点</b><span class="spacer"></span><span>梯队 × 概念</span></div>
            <div class="card-media">
              <div class="screen">
                <div class="tm-top">
                  <b>涨停热点</b><span class="dim">连板梯队 × 概念板块分布</span>
                  <span class="spacer"></span><span class="dim">示意 · 2026-08-19</span>
                </div>
                <div class="lu-stats">
                  <span v-for="s in limitStats" :key="s.label" class="lu-stat" :style="{ borderTopColor: s.color }">
                    <b :style="{ color: s.color }">{{ s.value }}</b><i>{{ s.label }}</i>
                  </span>
                </div>
                <div class="lu-tags">
                  <span v-for="t in limitTags" :key="t.name">{{ t.name }} <b>{{ t.n }}</b></span>
                </div>
                <div v-for="l in limitLadder" :key="l.tier" class="lu-row">
                  <span class="lu-tier" :style="{ background: l.color }">{{ l.tier }}</span>
                  <span class="dim">{{ l.count }}</span>
                  <span class="up">{{ l.names }}</span>
                </div>
              </div>
            </div>
            <div class="card-copy">
              <h3>钱堆在哪一档高度</h3>
              <ul class="ticks">
                <li>涨停、跌停、最高连板、一字板同屏，情绪冷热不用猜</li>
                <li>概念板块按涨停家数排布，主线和杂毛分得开</li>
                <li>连板梯队逐档展开，看得出是在发酵还是已经见顶</li>
              </ul>
            </div>
          </div>

          <!-- 风险预警 -->
          <div class="card">
            <div class="win-head"><b>风险预警</b><span class="spacer"></span><span>四档红绿灯</span></div>
            <div class="card-media">
              <div class="screen">
                <div class="au-top">
                  <span class="k">全市场风险分</span><span class="v">49.2</span><span class="warn strong">警惕</span>
                  <span class="spacer"></span><span class="m dim">示意</span>
                </div>
                <div class="tiers wide">
                  <i class="t-safe"></i><i class="t-warn"></i><i class="t-danger"></i><i class="t-crit"></i>
                </div>
                <div class="tier-labels">
                  <span>安全</span><span class="warn strong">警惕 ←</span><span>危险</span><span>极危</span>
                </div>
                <div class="tri wide">
                  <span v-for="s in ovRisk" :key="s.label"><b :class="s.tone">{{ s.value }}</b><i>{{ s.label }}</i></span>
                </div>
                <p class="screen-say bordered">
                  强弱分化明显，普涨不成立。<b>对应动作：控制单票仓位，优先主线。</b>
                </p>
              </div>
            </div>
            <div class="card-copy">
              <h3>风险分不是一个形容词</h3>
              <ul class="ticks">
                <li>四档各自写明它在历史上对应什么市场状况，以及该做什么动作</li>
                <li>风险分拆成几项分别显示，看得到它为什么给出这个结论</li>
                <li>同时给历史依据，不是凭一句话让你减仓</li>
              </ul>
            </div>
          </div>

          <!-- 个股深研 -->
          <div class="card">
            <div class="win-head"><b>个股深研</b><span class="spacer"></span><span>输入代码即可</span></div>
            <div class="card-media">
              <div class="screen">
                <div class="st-top">
                  <b class="name">贵州茅台</b>
                  <span class="chip">600519</span>
                  <span class="price">1297.99</span>
                  <span class="dim">量化 55 · 综合 45</span>
                  <span class="spacer"></span>
                  <span class="chip warn">结构存疑</span>
                  <span class="dim">示意</span>
                </div>
                <div class="st-dims">
                  <span v-for="d in stockDims" :key="d.name" class="barline">
                    <span class="bl-name wide">{{ d.name }}</span>
                    <span class="bl-track"><i :style="{ width: d.w + '%', background: d.color }"></i></span>
                    <span class="bl-val" :style="{ color: d.color }">{{ d.verdict }}</span>
                  </span>
                </div>
                <div class="st-chart">
                  <div class="st-chart-head">
                    <span class="dim">价格走势（近 40 日，可缩放）</span>
                    <span class="spacer"></span>
                    <span class="ma ma5">━ MA5</span><span class="ma ma10">━ MA10</span><span class="ma ma20">━ MA20</span>
                  </div>
                  <svg viewBox="0 0 900 300" role="img" aria-label="示意价格走势图">
                    <g>
                      <line v-for="g in kline.grid" :key="'g' + g.y" class="k-grid" x1="44" :y1="g.y" x2="890" :y2="g.y" />
                      <text v-for="g in kline.grid" :key="'t' + g.y" class="k-axis" x="36" :y="g.y + 4">{{ g.label }}</text>
                    </g>
                    <g v-for="(c, i) in kline.candles" :key="i">
                      <line :x1="c.x" :y1="c.hy" :x2="c.x" :y2="c.ly" :class="c.up ? 'k-up-s' : 'k-down-s'" />
                      <rect :x="c.bx" :y="c.by" :width="kline.bw" :height="c.bh" :class="c.up ? 'k-up' : 'k-down'" />
                    </g>
                    <polyline class="k-ma5" :points="kline.ma5" />
                    <polyline class="k-ma10" :points="kline.ma10" />
                    <polyline class="k-ma20" :points="kline.ma20" />
                    <text v-for="d in kline.dates" :key="d.label" class="k-axis mid" :x="d.x" y="276">{{ d.label }}</text>
                  </svg>
                </div>
              </div>
            </div>
            <div class="card-copy">
              <h3>手里的票现在是什么状态</h3>
              <ul class="ticks">
                <li>给出综合结论和分项打分，而不是一句「看好」或「看空」</li>
                <li>带均线的走势图，关键位置直接标在图上</li>
                <li>财务速览、市场表现、近期相关新闻同屏，不用另开三个网站</li>
              </ul>
            </div>
          </div>

          <!-- 自选股 + 数据中心 -->
          <div class="card">
            <div class="win-head"><b>我的自选股 · 数据中心</b><span class="spacer"></span><span>常驻</span></div>
            <div class="card-copy bordered">
              <h3>我的自选股</h3>
              <ul class="ticks">
                <li>收藏个股，实时跟踪涨跌与触发的预警</li>
                <li>名单里的票可一键加入，不用手抄代码</li>
              </ul>
            </div>
            <div class="card-copy">
              <h3>数据中心</h3>
              <ul class="ticks">
                <li>本地全市场日线的覆盖范围与更新状态，看得到数据本身</li>
                <li>回放用的就是这份库，不是另一套「演示数据」</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 05 一个交易日 -->
    <section id="day" class="band band-alt" data-reveal>
      <div class="wrap">
        <div class="sec-head tight">
          <span class="sec-no">05 / A DAY</span>
          <h2>一个交易日怎么用它</h2>
          <span class="sec-sub">板块不是并列的功能清单，是一条有先后的线</span>
        </div>
        <p class="sec-lede">产品里的每一块都对应盘中的一个时点。按这条线走，不用记菜单在哪。</p>
        <div class="timeline">
          <div v-for="d in dayline" :key="d.at" class="tl-step">
            <span class="tl-at">{{ d.at }}</span>
            <h3>{{ d.board }}</h3>
            <p>{{ d.body }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- 06 否决记录：整页最不可替代的一节。 -->
    <section id="rejected" class="band band-wash" data-reveal>
      <div class="wrap">
        <div class="sec-head tight">
          <span class="sec-no">06 / REJECTED</span>
          <h2>已否决假设登记表</h2>
          <span class="sec-sub">做加法容易，做减法要有证据</span>
        </div>
        <p class="sec-lede">
          下面每一条都真的测过、真的想接，最后被上面那七道闸拦下来。
          没有一家荐股平台会告诉你这些，因为承认试错就等于承认自己不是神。
        </p>
        <div class="table reject">
          <div class="tr th"><span>方向</span><span>裁决日</span><span>结论</span></div>
          <div v-for="r in rejected" :key="r.name" class="tr">
            <span class="rj-name">{{ r.name }}</span>
            <span class="rj-date">{{ r.date }}</span>
            <span class="rj-body">{{ r.verdict }}</span>
          </div>
        </div>
        <p class="table-foot">登记表随每次裁决更新。裁决标准在动手之前就定好，不会事后改标准来迁就结果。</p>
      </div>
    </section>

    <!-- 07 适合谁 -->
    <section class="band" data-reveal>
      <div class="wrap">
        <div class="sec-head">
          <span class="sec-no">07 / FIT</span>
          <h2>适合谁 · 不适合谁</h2>
          <span class="sec-sub">与其事后失望，不如现在就说清</span>
        </div>
        <div class="cols-2 ruled">
          <div class="col">
            <h3 class="fit-yes">MATCH · 适合</h3>
            <ul class="ticks">
              <li v-for="f in fitYes" :key="f">{{ f }}</li>
            </ul>
          </div>
          <div class="col">
            <h3 class="fit-no">NO MATCH · 不适合</h3>
            <ul class="ticks muted">
              <li v-for="f in fitNo" :key="f">{{ f }}</li>
            </ul>
          </div>
        </div>
      </div>
    </section>

    <!-- 08 定价：整站免费，没有付费档。对外只陈述事实，不解释为什么。 -->
    <section id="pricing" class="band band-alt" data-reveal>
      <div class="wrap">
        <div class="sec-head tight">
          <span class="sec-no">08 / PRICING</span>
          <h2>不收费</h2>
        </div>
        <div class="pricing ruled">
          <div class="col">
            <p class="free-lead">全部功能免费。没有付费档，没有「高级版」，<strong>以后也不会有</strong>。</p>
            <p>这不是限时活动，也不是先免费后收割。所有选股池、每日名单、历史回放数据和复盘战绩都完整开放，不存在任何需要付费才能看到的部分。</p>
            <p>它是一个公开的个人研究项目：作者自己每天在用，把方法、数据和被否决的结论一并公开。没有推销，没有客服追单，也不会有人加你微信喊你充值。</p>
            <router-link class="btn btn-primary btn-lg" :to="loggedIn ? '/dashboard' : '/login?register=1'">
              {{ loggedIn ? '进入应用' : '免费开始' }}
            </router-link>
          </div>
          <div class="col">
            <div class="cap boxed">你能拿到什么</div>
            <ul class="ticks">
              <li v-for="f in freeItems" :key="f">{{ f }}</li>
            </ul>
          </div>
        </div>
      </div>
    </section>

    <!-- 09 FAQ -->
    <section class="band" data-reveal>
      <div class="wrap">
        <div class="sec-head tight">
          <span class="sec-no">09 / FAQ</span>
          <h2>常见问题</h2>
        </div>
        <div class="faq">
          <div v-for="f in faqs" :key="f.q" class="faq-row">
            <h3>{{ f.q }}</h3>
            <p v-html="f.a"></p>
          </div>
        </div>
      </div>
    </section>

    <!-- 页脚 -->
    <footer class="foot">
      <div class="wrap">
        <div class="foot-brand"><BrandLogo :size="22" /><span>ASTOCKPICK</span></div>
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
// 业绩数字来自 docs/superpowers/specs/2026-07-13-replay-validation-report.md 与
// experiments/ 的实测结论；板块示意图的数值来自 2026-08-19 的界面快照。
// 改数字前先回那两处核对口径。
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

// 负号统一用 U+2212，不用 hyphen —— 等宽栈里 hyphen 比数字窄，成列的数字会对不齐。
const fmt = (v: number) => (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(2)
const roman = ['i', 'ii', 'iii', 'iv', 'v']

const tickers = [
  { label: '上证指数', value: '3990.29', extra: '0.00%', tone: '' },
  { label: '深证成指', value: '14622.56', extra: '', tone: '' },
  { label: '创业板指', value: '3705.56', extra: '', tone: '' },
  { label: '涨跌', value: '1233 / 3207', extra: '', tone: '' },
  { label: '风险', value: '警惕 44.6', extra: '', tone: 'warn' },
]

const heroPicks = [
  { no: '01', name: '农发种业', code: '600313', score: 89, ret: 4.2 },
  { no: '02', name: '中粮糖业', code: '600737', score: 87, ret: -1.1 },
  { no: '03', name: '敦煌种业', code: '600354', score: 85, ret: 2.7 },
  { no: '04', name: '登海种业', code: '002041', score: 85, ret: 0.4 },
  { no: '05', name: '神农种业', code: '300189', score: 83, ret: -0.8 },
  { no: '06', name: '苏垦农发', code: '601952', score: 82, ret: 1.9 },
  { no: '07', name: '荃银高科', code: '300087', score: 81, ret: -2.4 },
]

const proofs = [
  { span: '12 个月回放 · 130 期 · 次日开盘可成交口径', value: '+1.99', tone: 'up', note: '平均超额 / 期 · 中位 +0.43pp · 票级胜率 51.9%' },
  { span: '60 个月长样本 · 231 期 · 收盘买入 T+5', value: '−0.62', tone: 'down', note: '平均超额 / 期 · t = −2.49 · 我们不挑对自己有利的那个' },
]

const steps = [
  {
    no: '01',
    title: '同源评分',
    // 只说「同源」这件事本身，不列因子构成和门槛数值 —— 那是选股逻辑，不对外写。
    body:
      '多维度结构因子合成打分，并设有流动性下限。关键在于：线上扫描与历史回放调用的是同一个评分函数。' +
      '具体因子构成与参数不对外披露。',
  },
  {
    no: '02',
    title: 'Point-in-time 回放',
    body:
      '每 2 个交易日一期、每期取 top20、T+5 相对全市场基准计超额。双口径并列：收盘回测价与次日开盘可成交价，' +
      '买不到的涨停票如实标注占比。',
  },
  {
    no: '03',
    title: '实盘留痕复盘',
    body:
      '每次扫描自动留痕，按真实行情统计 T+1 / T+3 / T+5。评分公式一旦变更即换池名，' +
      '旧公式的战绩不会挂到新公式名下，账不许混着算。',
  },
]

// 七道闸：只写闸门名称与它防的坑，阈值数值一律不对外。来源 experiments/rule_audit.py。
const gates = [
  { no: '01', name: '样本量', core: false, body: '笔数够，且分散在够多的交易日上。200 笔全挤在 3 天里，等于只有 3 个独立观测。' },
  { no: '02', name: '超额为正', core: false, body: '最低门槛。连这一关都过不了的规则，后面六关不用看。' },
  {
    no: '03',
    name: '匹配对照增量',
    core: true,
    body:
      '整套东西的核心。拿全市场当基准会把 beta 记成规则的 alpha —— 深跌 30% 的票本来就比全市场反弹得多。' +
      '改成跟「同一天、同样跌幅档、同样流动性档、但没触发信号」的票比：在同样处境的票里，这条规则挑出来的是不是更好。' +
      '按交易日聚类的置信区间下沿也必须为正。',
  },
  { no: '04', name: '右尾稳健', core: false, body: '砍掉最好的那一小撮之后仍要为正。拦的是「超额 100% 来自右尾」—— 均值好看，但你买中的概率极低。' },
  { no: '05', name: '时间稳定', core: false, body: '要有足够多的年份方向一致。拦的是「换个跨度就翻号」—— 本页那两个数字正是这个现象的活样本。' },
  { no: '06', name: '多重检验', core: false, body: 'Holm 校正后仍显著，且按同一批提交的规则一起校正。分多次跑再挑好看的那条，等于自己把校正绕过去了。' },
  { no: '07', name: '扣摩擦为正', core: false, body: '扣掉双边交易成本之后还得是正的。纸上赢、账上输的规则不算赢。' },
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
const chart = { w: 900, h: 300, barW: 70, zeroY: 96, scale: 70 }

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
  '滑点未计：双边交易成本（约 0.13pp / 期）已扣，冲击成本与滑点没有。',
  '窗口敏感：会话轴前移 3 周即可让部分结论翻转，任何单次回放数字都不能当承诺。',
  '排除规则用当前股票名称，历史时点的 ST 状态无法还原。',
  '竞价优选依赖盘中数据，日线无法 point-in-time 重建，目前只有实盘留痕，样本不足以下结论。',
]

// ── 盘面总览示意面板 ──────────────────────────────────────────
const ovLimit = [
  { value: '34', label: '涨停', tone: 'up' },
  { value: '22', label: '跌停', tone: 'down' },
  { value: '4', label: '最高板', tone: '' },
]
const ovRisk = [
  { value: '122', label: '退出 / 止损', tone: 'up' },
  { value: '351', label: '减仓防守', tone: 'up' },
  { value: '7', label: '反包观察', tone: '' },
]
const ovAuction = [
  { value: '0', label: '主力抢筹' },
  { value: '0', label: '洗盘低吸' },
  { value: '0', label: '诱多出货' },
]
const ovHeat = [
  { name: '航天装备Ⅱ', w: 88, value: '+6.29%', tone: 'up' },
  { name: '风电设备', w: 40, value: '+2.57%', tone: 'up' },
  { name: '种植业', w: 38, value: '+2.44%', tone: 'up' },
  { name: '电子化学品Ⅱ', w: 66, value: '−4.62%', tone: 'down' },
  { name: '通信设备', w: 63, value: '−4.48%', tone: 'down' },
]
const ovReview = [
  { name: '竞价优选', cells: [{ v: '55%', tone: 'up' }, { v: '62%', tone: 'up' }, { v: '56%', tone: 'up' }] },
  { name: '智能选股', cells: [{ v: '50%', tone: 'up' }, { v: '47%', tone: 'down' }, { v: '45%', tone: 'down' }] },
]

// ── 智能选股示意面板 ──────────────────────────────────────────
const smartCols = ['综合排序', '结构分', '时机确认', '代码', '名称', '行业', '现价', '涨跌幅', '价位参考']
const smartRows = [
  { rank: '88.6', struct: '83.7', timing: '当日量确认', confirmed: true, code: '600313', name: '农发种业', industry: '农业产品', price: '7.46', chg: '+10.03%', ref: '触发 7.46 · 上沿 8.19<br>下沿 6.98 · 敞口比 1.5:1' },
  { rank: '86.7', struct: '87.0', timing: '等待量价确认', confirmed: false, code: '600737', name: '中粮糖业', industry: '食品添加剂', price: '15.80', chg: '+4.77%', ref: '结构入选，待时机确认' },
  { rank: '85.4', struct: '85.0', timing: '等待量价确认', confirmed: false, code: '600354', name: '敦煌种业', industry: '农业产品', price: '7.34', chg: '+5.01%', ref: '结构入选，待时机确认' },
  { rank: '84.6', struct: '81.9', timing: '等待量价确认', confirmed: false, code: '002041', name: '登海种业', industry: '农业产品', price: '10.50', chg: '+7.14%', ref: '结构入选，待时机确认' },
  { rank: '83.4', struct: '82.4', timing: '等待量价确认', confirmed: false, code: '300189', name: '神农种业', industry: '农业产品', price: '6.53', chg: '+4.82%', ref: '结构入选，待时机确认' },
]

// ── 选股复盘示意面板：真实留痕快照，含难看的那一行 ────────────
const reviewPools = [
  {
    name: '智能选股',
    marks: '636 条留痕',
    cells: [
      { h: 'T+1', win: '50%', avg: '+0.53%', ex: '+0.33pp', n: '600', tone: 'up' },
      { h: 'T+3', win: '47%', avg: '+0.45%', ex: '−0.67pp', n: '522', tone: 'down' },
      { h: 'T+5', win: '45%', avg: '−0.06%', ex: '−2.28pp', n: '441', tone: 'down' },
    ],
  },
  {
    name: '竞价优选',
    marks: '295 条留痕',
    cells: [
      { h: 'T+1', win: '55%', avg: '+1.41%', ex: '+0.98pp', n: '280', tone: 'up' },
      { h: 'T+3', win: '62%', avg: '+2.58%', ex: '+1.02pp', n: '250', tone: 'up' },
      { h: 'T+5', win: '56%', avg: '+1.94%', ex: '−0.88pp', n: '220', tone: 'up' },
    ],
  },
  {
    name: '时机融合 v1',
    marks: '459 条留痕',
    cells: [
      { h: 'T+1', win: '52%', avg: '+1.00%', ex: '+0.80pp', n: '423', tone: 'up' },
      { h: 'T+3', win: '51%', avg: '+1.78%', ex: '+0.81pp', n: '345', tone: 'up' },
      { h: 'T+5', win: '50%', avg: '+2.05%', ex: '+0.41pp', n: '264', tone: 'up' },
    ],
  },
]

// ── 集合竞价示意面板 ──────────────────────────────────────────
const auctionKv = [
  { label: '高开 / 低开', value: '<b class="up">721</b> / <b class="down">4189</b>' },
  { label: '高开比', value: '<b>13.5%</b>' },
  { label: '平均开盘', value: '<b class="down">−1.02%</b>' },
  { label: '竞价涨停 / 跌停', value: '<b class="up">9</b> / <b class="down">7</b>' },
]
const openDist = [
  { n: 9, o: 1, tone: 'up' },
  { n: 22, o: 0.55, tone: 'up' },
  { n: 69, o: 0.3, tone: 'up' },
  { n: 621, o: 0.14, tone: 'up' },
  { n: 415, o: 1, tone: 'flat' },
  { n: 3665, o: 0.3, tone: 'down' },
  { n: 531, o: 1, tone: 'down' },
]

// ── 行业热力 treemap：面积≈市值权重，颜色按红涨绿跌 ────────────
const treemap = [
  {
    h: 74,
    cells: [
      { name: '银行Ⅱ', value: '+0.07%', w: 30, bg: '#3b4048' },
      { name: '半导体', value: '−3.17%', w: 26, bg: '#1f7a52' },
      { name: '通信设备', value: '−4.25%', w: 17, bg: '#0f7a3d' },
      { name: '电力', value: '−0.14%', w: 14, bg: '#343a42' },
      { name: '电池', value: '−1.43%', w: 13, bg: '#2c5f47' },
    ],
  },
  {
    h: 50,
    cells: [
      { name: '消费电子', value: '−2.57%', w: 20, bg: '#237f56' },
      { name: '汽车零部件', value: '−0.17%', w: 15, bg: '#343a42' },
      { name: '保险Ⅱ', value: '+0.07%', w: 13, bg: '#3b4048' },
      { name: '化学制药', value: '−0.3%', w: 13, bg: '#343a42' },
      { name: '煤炭开采', value: '+0.15%', w: 13, bg: '#4a3236' },
      { name: '通用设备', value: '−1.19%', w: 13, bg: '#2c5f47' },
      { name: '电网设备', value: '−0.63%', w: 13, bg: '#31544a' },
    ],
  },
  {
    h: 44,
    cells: [
      { name: '证券Ⅱ', value: '−0.17%', w: 16, bg: '#343a42' },
      { name: '元件', value: '−3.79%', w: 14, bg: '#12854a' },
      { name: '专用设备', value: '−0.96%', w: 12, bg: '#2f5a48' },
      { name: '医疗器械', value: '−0.37%', w: 12, bg: '#343a42' },
      { name: '航运港口', value: '+1.43%', w: 12, bg: '#6b2a2f' },
      { name: '白色家电', value: '−0.32%', w: 12, bg: '#343a42' },
      { name: '小金属', value: '−2.3%', w: 11, bg: '#25795a' },
      { name: '贵金属', value: '−2.91%', w: 11, bg: '#1f7a52' },
    ],
  },
]

// ── 涨停热点示意面板 ──────────────────────────────────────────
const limitStats = [
  { value: '34', label: '今日涨停', color: '#c2101f' },
  { value: '22', label: '今日跌停', color: '#0f7a3d' },
  { value: '4', label: '最高连板', color: '#b06f12' },
  { value: '20', label: '2 板以上', color: '#3b6fb5' },
  { value: '34', label: '一字板', color: '#1a1712' },
]
const limitTags = [
  { name: '光通信/CPO', n: 1 }, { name: '存储', n: 1 }, { name: '国产芯片', n: 1 }, { name: '机器人', n: 1 },
  { name: '汽车产业链', n: 2 }, { name: '有色金属', n: 2 }, { name: '化工材料', n: 1 }, { name: '电力能源', n: 3 },
  { name: '医药', n: 2 }, { name: '港口航运', n: 1 }, { name: '地产链', n: 1 }, { name: '大消费', n: 4 },
  { name: '航天军工', n: 1 }, { name: '其他', n: 13 },
]
const limitLadder = [
  { tier: '4 连板', count: '2 只', color: '#b06f12', names: '桂发祥 · XD正裕工' },
  { tier: '3 连板', count: '1 只', color: '#b8863b', names: '京粮控股' },
  { tier: '2 连板', count: '17 只', color: '#0f7a3d', names: '盈新发展 · 亚太股份 · 物产环能 · 浙江东日 · 学大教育 …' },
]

// ── 个股深研示意面板 ──────────────────────────────────────────
const stockDims = [
  { name: '七不买风险', w: 62, color: '#b06f12', verdict: '单项风险' },
  { name: '趋势均线', w: 34, color: '#c2101f', verdict: '空头向下' },
  { name: '量能', w: 92, color: '#0f7a3d', verdict: '活跃' },
  { name: '资金（价量代理）', w: 24, color: '#c2101f', verdict: '净流出' },
  { name: '盘面/情绪（大盘）', w: 52, color: '#7b7469', verdict: '中性' },
]

// K 线为示意走势，不代表任何标的。数据写死，避免每次渲染都变。
const ohlc: number[][] = [
  [1195.0, 1197.2, 1186.7, 1192.9], [1192.9, 1198.2, 1183.8, 1187.8], [1187.8, 1192.8, 1181.2, 1182.5],
  [1182.5, 1184.0, 1179.9, 1181.7], [1181.7, 1189.3, 1178.8, 1180.7], [1180.7, 1186.8, 1168.8, 1177.4],
  [1177.4, 1182.5, 1168.6, 1178.4], [1178.4, 1186.2, 1169.6, 1172.9], [1172.9, 1174.9, 1168.0, 1171.5],
  [1171.5, 1184.6, 1165.9, 1182.2], [1182.2, 1193.7, 1176.8, 1189.7], [1189.7, 1191.2, 1184.2, 1186.8],
  [1186.8, 1199.5, 1183.3, 1195.1], [1195.1, 1206.2, 1191.7, 1201.6], [1201.6, 1218.5, 1198.7, 1211.9],
  [1211.9, 1223.5, 1203.9, 1218.3], [1218.3, 1230.7, 1209.4, 1227.4], [1227.4, 1231.7, 1218.5, 1225.5],
  [1225.5, 1230.4, 1222.9, 1224.2], [1224.2, 1239.4, 1218.7, 1232.3], [1232.3, 1247.5, 1225.7, 1244.0],
  [1244.0, 1256.4, 1239.4, 1250.7], [1250.7, 1270.4, 1245.9, 1261.9], [1261.9, 1271.3, 1255.2, 1269.8],
  [1269.8, 1286.4, 1262.2, 1277.5], [1277.5, 1282.7, 1271.1, 1278.6], [1278.6, 1283.3, 1263.6, 1266.0],
  [1266.0, 1267.5, 1247.9, 1255.1], [1255.1, 1258.1, 1240.3, 1244.4], [1244.4, 1248.7, 1239.8, 1247.1],
  [1247.1, 1255.2, 1236.4, 1244.0], [1244.0, 1249.8, 1239.7, 1246.5], [1246.5, 1254.6, 1231.3, 1240.0],
  [1240.0, 1242.4, 1226.9, 1229.7], [1229.7, 1234.6, 1215.2, 1220.9], [1220.9, 1222.0, 1208.3, 1212.6],
  [1212.6, 1218.2, 1197.7, 1206.3], [1206.3, 1211.4, 1199.8, 1205.7], [1205.7, 1207.2, 1196.7, 1204.9],
  [1204.9, 1213.9, 1197.5, 1205.9],
]

const kline = computed(() => {
  const n = ohlc.length
  const W = 900
  const H = 300
  const L = 44
  const R = 10
  const T = 12
  const B = 44
  const closes = ohlc.map((r) => r[3])
  const lows = ohlc.map((r) => r[2])
  const highs = ohlc.map((r) => r[1])
  const pad = (Math.max(...highs) - Math.min(...lows)) * 0.12
  const lo = Math.min(...lows) - pad
  const hi = Math.max(...highs) + pad
  const X = (i: number) => L + ((W - L - R) * (i + 0.5)) / n
  const Y = (p: number) => T + ((H - T - B) * (hi - p)) / (hi - lo)
  const bw = ((W - L - R) / n) * 0.58
  const ma = (k: number, i: number) => {
    const s = closes.slice(Math.max(0, i - k + 1), i + 1)
    return s.reduce((a, b) => a + b, 0) / s.length
  }
  const line = (k: number) => ohlc.map((_, i) => `${X(i).toFixed(1)},${Y(ma(k, i)).toFixed(1)}`).join(' ')

  return {
    bw,
    grid: [0, 0.25, 0.5, 0.75, 1].map((f) => ({
      y: T + (H - T - B) * f,
      label: Math.round(hi - (hi - lo) * f).toLocaleString('en-US'),
    })),
    candles: ohlc.map(([o, h, l, c], i) => {
      const top = Y(Math.max(o, c))
      const bottom = Y(Math.min(o, c))
      return {
        up: c >= o,
        x: X(i),
        hy: Y(h),
        ly: Y(l),
        bx: X(i) - bw / 2,
        by: top,
        bh: Math.max(1.4, bottom - top),
      }
    }),
    ma5: line(5),
    ma10: line(10),
    ma20: line(20),
    dates: [0, 9, 19, 29, 39].map((i) => ({ x: X(i), label: `07-${String((i % 28) + 1).padStart(2, '0')}` })),
  }
})

const dayline = [
  { at: '09:25', board: '集合竞价', body: '撮合一结束就给出当日情绪档位，并说明这个档位历史上对应什么状况。先定性，再动手。' },
  { at: '开盘后', board: '智能选股', body: '点一次跑全市场，出当日名单并当场留痕。整池等权买入，不挑一两只重仓。' },
  { at: '盘中', board: '风险预警 · 行业热力', body: '风险分四档红绿灯给出明确的仓位动作；热力图看钱去了哪些行业。' },
  { at: '15:00 收盘', board: '涨停热点', body: '钱堆在哪一档高度、哪一个方向。连板梯队看得出是在发酵还是已经见顶。' },
  { at: 'T+1 / T+3 / T+5', board: '选股复盘', body: '按真实行情算账。哪天推了什么、后来怎么走，逐条对得上。' },
]

// 否决登记表。结论来自 experiments/ 的裁决记录，改动前先回那份文档核对。
// 只留「测过什么、结论是什么」；效应量、t 值与预登记标准属于方法论，不对外写。
const rejected = [
  {
    name: '板块量能扩张',
    date: '2026-08-28',
    verdict: '产品自己原有的板块因子，主导项就是它。审出来没有独立 alpha，一直靠旁边一个小系数在扛。系数已按结果重配 —— 这一条是自己拆自己的台。',
  },
  {
    name: '追高闸门七个候选',
    date: '2026-08-25',
    verdict: '想给「刚拉完贴着高点」的票加一道闸。七个候选做剂量反应，只在某一档有效、邻档无效 —— 那是噪音不是机制。改成如实标注，不动排序。',
  },
  {
    name: '主升浪开启形态',
    date: '2026-08-13',
    verdict: '民间流传的那套四段式形态。它更容易出大牛股，但整体是负期望，而产品吃的是组合平均。不接入排序。',
  },
  {
    name: '缩量埋伏',
    date: '2026-08-05',
    verdict: '收盘口径下极好看，换成用户真能买到的入场点就归零。差额全在一段吃不到的跳空里，不接。',
  },
  {
    name: '龙虎榜数据源',
    date: '2026-08-03',
    verdict: '接过一版，跑出来的正结果经不起复核，判定为多重检验的产物。不接这个源。',
  },
  {
    name: '强势池四条硬筛',
    date: '2026-08-03',
    verdict: '看着有四道筛子，逐条查下来全都不是约束，实际只剩「买最极端的那些」。同口径下没有 alpha，十余条替代规则同样全败。',
  },
  {
    name: '以中位数为目标',
    date: '2026-07-29',
    verdict: '想把「大多数票都能赚」做成目标。加闸门、改风控、重搜权重全部失败，票级胜率纹丝不动。现有体系内做不到，别再试。',
  },
  {
    name: '四个权重变体',
    date: '2026-07-25',
    verdict: '想让排序更「科学」，四个变体在长样本上全部劣于现行权重。现行权重不改。',
  },
]

const fitYes = [
  '接受整池等权、按规则执行的人',
  '会自己看数据判断、而不是要一个「答案」的人',
  '能接受连续几个月不跑赢，也不推翻规则的人',
]
const fitNo = [
  '想要「明天涨停的票」的人',
  '需要收益承诺或保本的人',
  '只挑一两只重仓、把组合信号当个股信号用的人',
]

const freeItems = [
  '全部选股池与每日名单，条数不因免费而缩水',
  '集合竞价 / 行业热力 / 涨停热点 / 风险预警',
  '个股深研 / 选股复盘 / 自选股 / 数据中心',
  '历史回放数据与全部复盘战绩，含为负的那部分',
  '不限次数，没有任何需要付费才能看到的部分',
]

const faqs = [
  {
    q: '完全免费，会不会以后再收费？',
    a: '不会。这是一个作者自己每天在用的研究项目，本身没有靠它赚钱的打算，所以也不存在「先养用户再收割」这条路径。',
  },
  {
    q: '这是荐股服务吗？',
    a:
      '不是。本产品不具备证券投资咨询资质，输出的是算法对公开数据的处理结果：评分、信号与统计，' +
      '不构成投资建议，也不提供代客理财。买卖决策与后果由你自己承担。',
  },
  {
    q: '为什么产品里没有「盘中实时机会雷达」？',
    a:
      '有过，撤了。盘中信号依赖分钟级数据，日线没法 point-in-time 重建，也就过不了上面那七道闸 —— ' +
      '只有实盘留痕，样本不足以下结论。<strong>一个自己都验不了的板块，留在产品里就是在替它背书。</strong>',
  },
  {
    q: '多久上一条新规则？',
    a:
      '没有节奏，也不追进度。过七道闸就上，过不了就不上 —— 迄今为止通过的只有一条，否决的全在上面的登记表里。' +
      '「这个月又上了三个新功能」不是我们要的东西。',
  },
  {
    q: '为什么胜率只有 51.9%，中位数还这么小？',
    a:
      '因为真实情况就是这样。A 股短线信号的单票胜率天然接近抛硬币，超额主要来自组合层面。' +
      '把胜率标到 80% 的产品，要么在挑窗口，要么没做过 point-in-time 回放。',
  },
  {
    q: '为什么要整池买而不是挑一两只？',
    a:
      '回放数据显示收益结构是「组合有效、单票分布很宽」：p10 与 p90 相差超过 20 个点。' +
      '挑一两只等于把组合信号当个股信号用，大概率吃到的是分布的左半边。',
  },
  {
    q: '数据从哪来，会不会实时？',
    a:
      '日线以腾讯行情为主、akshare 兜底，本地全市场落库并增量更新；盘中实时行情用于停牌 / ST 排除与涨跌展示。' +
      '数据来自公开渠道，不保证实时性、准确性与完整性。',
  },
]
</script>

<style scoped>
/* 交易终端式版面：暖纸底、墨黑字、等宽标题、栅格竖线、通栏横线。
   刻意不用 Element Plus 组件 —— 落地页与应用内是两套视觉语言，混用会两头不像。

   ── 只有两个数据色 ──
   红涨绿跌是 A 股口径，而品牌红与「涨」同族，所以强调色与语义色天然统一，
   不需要为了「设计感」再引入第三个跟金融语义打架的颜色。
   琥珀 --warn 只用在「警惕」这一个语义档位上，不作装饰。

   ── 字体 ──
   等宽栈必须带中文回退：ui-monospace / Consolas 都没有汉字，混排时中文会掉进宋体，
   成为全页唯一的衬线字。这里不引 Google Fonts —— 落地页主要访客在境内，
   fonts.googleapis.com 不可达，等首屏文字等到超时得不偿失。

   ── 板块示意图 ──
   §04 里的产品版面全部由 DOM + SVG 画成，没有位图。仓库里的界面截图只有 1 倍分辨率，
   放大即糊；且部分区域含不对外披露的判定口径。重绘同时解决了这两件事。 */
.site {
  --ink: #1a1712;
  --ink-2: #4b4640;
  --ink-3: #7b7469;
  --paper: #faf8f3;
  --paper-2: #f2efe7;
  --surface: #fffdf9;
  --rule: #ddd6c8;
  --rule-soft: #eae5da;
  --up: #c2101f;
  --up-ink: #8d0b16;
  --up-wash: #fbf0ef;
  --down: #0f7a3d;
  --down-wash: #eef7f1;
  --warn: #b06f12;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, 'PingFang SC', 'Microsoft YaHei', monospace;

  background: var(--paper);
  color: var(--ink);
  font-family: system-ui, -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1320px; margin: 0 auto; padding: 0 44px; }
.spacer { flex-grow: 1; }
.up { color: var(--up); }
.down { color: var(--down); }
.warn { color: var(--warn); }
.dim { color: var(--ink-3); }
.strong { font-weight: 600; }
.r { text-align: right; }
.mono { font-family: var(--mono); }

/* 入场：只做 8px 抬起 + 透明度。JS 只在 prefers-reduced-motion 未开启时挂载，
   降级路径是「类根本不会被加上」。 */
[data-reveal] { transition: opacity .5s cubic-bezier(.16,1,.3,1), transform .5s cubic-bezier(.16,1,.3,1); }
[data-reveal].is-pending { opacity: 0; transform: translateY(8px); }

/* ── 顶栏 ── */
.nav { border-bottom: 1px solid var(--rule); background: var(--paper); position: sticky; top: 0; z-index: 10; }
.nav-inner { display: flex; align-items: center; gap: 28px; height: 64px; }
.brand { display: flex; align-items: center; gap: 10px; text-decoration: none; color: var(--ink); }
.brand-text { font-family: var(--mono); font-weight: 700; font-size: 15px; letter-spacing: .5px; }
.links { display: flex; gap: 20px; font-family: var(--mono); font-size: 12px; }
.links a { color: var(--ink-2); text-decoration: none; padding-bottom: 2px; border-bottom: 1px solid transparent; transition: .16s; }
.links a:hover { color: var(--up-ink); border-bottom-color: var(--up-ink); }
.nav-cta { display: flex; align-items: center; gap: 14px; margin-left: auto; }
.btn-text { font-family: var(--mono); font-size: 12px; color: var(--ink-2); text-decoration: none; }
.btn-text:hover { color: var(--up-ink); }

.btn {
  display: inline-block; font-family: var(--mono); font-size: 12px; padding: 8px 18px;
  border-radius: 2px; text-decoration: none; border: 1px solid transparent; white-space: nowrap;
  transition: transform .12s, box-shadow .18s, border-color .18s, color .18s;
}
.btn-primary { background: var(--up); color: #fff; }
.btn-primary:hover { box-shadow: 0 6px 18px -6px rgba(194, 16, 31, .45); }
.btn-primary:active { transform: translateY(1px); box-shadow: none; }
.btn-ghost { border-color: var(--rule); color: var(--ink); background: var(--surface); }
.btn-ghost:hover { border-color: var(--up-ink); color: var(--up-ink); }
.btn-lg { font-size: 13px; padding: 13px 28px; }

/* ── 行情条 ── */
.ticker { border-bottom: 1px solid var(--rule); background: var(--paper-2); }
.ticker-inner { display: flex; font-family: var(--mono); font-size: 11.5px; color: var(--ink-3); }
.ticker-cell { flex-grow: 1; padding: 8px 16px; border-right: 1px solid var(--rule); }
.ticker-cell:first-child { padding-left: 0; }
.ticker-cell:last-child { border-right: 0; padding-right: 0; }
.ticker-cell b { color: var(--ink); font-weight: 500; }
.ticker-cell b.warn { color: var(--up-ink); font-weight: 600; }
.ticker-cell em { font-style: normal; margin-left: 6px; color: var(--ink-3); }

/* ── 首屏 ── */
.hero { border-bottom: 1px solid var(--rule); background: var(--surface); }
.hero-grid { display: grid; grid-template-columns: minmax(0, 1fr) 400px; }
.hero-copy { padding: 58px 40px 52px 0; }
.eyebrow { font-family: var(--mono); font-size: 11px; letter-spacing: 2.4px; color: var(--up-ink); margin: 0 0 20px; }
.hero h1 {
  font-family: var(--mono); font-weight: 700; font-size: clamp(28px, 3.2vw, 42px);
  line-height: 1.32; letter-spacing: -1.4px; margin: 0 0 20px;
}
.hero h1 .comma { color: var(--ink-3); }
.lede { font-size: 16px; line-height: 1.85; color: var(--ink-2); margin: 0 0 30px; }
.hero-cta { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 26px; }
.hero-free { font-family: var(--mono); font-size: 11.5px; color: var(--ink-3); margin: 0 0 22px; }
.hero-note {
  font-size: 12.5px; line-height: 1.85; color: var(--ink-3); margin: 0;
  padding-left: 13px; border-left: 2px solid var(--rule);
}
.hero-note strong { color: var(--ink-2); font-weight: 600; }

.hero-panel { padding: 22px 0 22px 32px; border-left: 1px solid var(--rule); }
.panel-cap {
  display: flex; align-items: baseline; gap: 10px; margin-bottom: 14px;
  font-family: var(--mono); font-size: 11px; color: var(--ink-3); letter-spacing: 1.2px;
}
.panel-cap .strong { color: var(--ink); font-weight: 500; }
.pick-head, .pick-row {
  display: grid; grid-template-columns: 22px minmax(0, 1fr) 56px 44px; gap: 8px;
  font-family: var(--mono); font-size: 12.5px;
}
.pick-head { padding: 9px 0; border-bottom: 1px solid var(--rule); color: var(--ink-3); font-size: 10.5px; letter-spacing: 1px; }
.pick-row { padding: 10px 0; border-bottom: 1px solid var(--rule-soft); }
.panel-foot { font-family: var(--mono); font-size: 10.5px; line-height: 1.8; color: var(--ink-3); margin: 14px 0 0; }

/* ── 证据条 ── */
.evidence { border-bottom: 1px solid var(--rule); background: var(--paper-2); }
.evidence-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.evidence-cell { padding: 32px 36px 32px 0; display: flex; flex-direction: column; gap: 6px; }
.evidence-cell:first-child { border-right: 1px solid var(--rule); }
.evidence-cell:last-child { padding: 32px 0 32px 36px; }
.evidence-cell .cap { font-family: var(--mono); font-size: 11px; letter-spacing: 1.4px; color: var(--ink-3); }
.evidence-cell .big {
  font-family: var(--mono); font-weight: 700; font-size: clamp(34px, 3.6vw, 50px);
  line-height: 1.1; letter-spacing: -2.5px;
}
.evidence-cell .big em { font-style: normal; font-size: 18px; letter-spacing: 0; }
.evidence-cell .sub { font-size: 13px; color: var(--ink-2); }

/* ── 区块骨架 ── */
.band { border-bottom: 1px solid var(--rule); padding-bottom: 36px; }
.band-alt { background: var(--paper-2); }
.band-wash { background: var(--up-wash); }
.sec-head { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; padding: 32px 0 26px; }
.sec-head.tight { padding-bottom: 0; }
.sec-no { font-family: var(--mono); font-size: 11px; letter-spacing: 2px; color: var(--up); }
.sec-head h2 { font-family: var(--mono); font-weight: 700; font-size: clamp(20px, 2.2vw, 26px); margin: 0; letter-spacing: -.6px; }
.sec-sub { font-size: 13px; color: var(--ink-3); }
.sec-lede { font-size: 14px; line-height: 1.9; color: var(--ink-2); margin: 14px 0 24px; max-width: 54em; }
.sec-lede.muted { font-size: 13px; color: var(--ink-3); }
.sec-lede strong { color: var(--ink); font-weight: 600; }
.rule-left { padding-left: 14px; border-left: 2px solid var(--rule); }

.cols-3, .cols-2 { display: grid; }
.cols-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.ruled { border-top: 1px solid var(--rule); }
.ruled .col { padding: 24px 28px 32px; border-right: 1px solid var(--rule); }
.ruled .col:first-child { padding-left: 0; }
.ruled .col:last-child { padding-right: 0; border-right: 0; }
.step-no { font-family: var(--mono); font-size: 11px; letter-spacing: 1.6px; color: var(--up-ink); }
.ruled .col h3 { font-family: var(--mono); font-weight: 600; font-size: 17px; margin: 8px 0 10px; }
.ruled .col p { font-size: 13.5px; line-height: 1.9; color: var(--ink-2); margin: 0; }

/* ── 表格（七道闸 / 否决登记表） ── */
.table { border-top: 2px solid var(--ink); }
.tr {
  display: grid; grid-template-columns: 40px 200px minmax(0, 1fr); gap: 24px;
  padding: 15px 0; border-bottom: 1px solid var(--rule-soft); align-items: baseline;
}
.tr.th { padding: 11px 0; border-bottom: 1px solid var(--rule); font-family: var(--mono); font-size: 10.5px; letter-spacing: 1.4px; color: var(--ink-3); }
.tr.core { background: var(--surface); }
.gate-no { font-family: var(--mono); font-size: 13px; color: var(--up); }
.gate-name { font-family: var(--mono); font-size: 14px; }
.gate-name em { display: block; font-style: normal; font-size: 10.5px; letter-spacing: 1.2px; color: var(--up-ink); }
.gate-body { font-size: 13px; line-height: 1.85; color: var(--ink-2); }
.callout { font-size: 13px; line-height: 1.9; color: var(--ink-2); margin: 18px 0 26px; padding-left: 14px; border-left: 2px solid var(--up); max-width: 54em; }
.callout strong { color: var(--ink); font-weight: 600; }

.passed {
  border: 1px solid var(--rule); border-left: 3px solid var(--down); background: var(--down-wash);
  padding: 22px 28px; display: grid; grid-template-columns: 240px minmax(0, 1fr); gap: 32px; align-items: start;
}
.passed .cap { font-family: var(--mono); font-size: 10.5px; letter-spacing: 1.4px; color: var(--down); }
.passed h3 { font-family: var(--mono); font-weight: 600; font-size: 16px; margin: 8px 0 0; line-height: 1.6; }
.passed p { font-size: 13.5px; line-height: 1.9; color: var(--ink-2); margin: 0; }
.passed strong { color: var(--ink); font-weight: 600; }

.reject.table .tr { grid-template-columns: 176px 118px minmax(0, 1fr); }
.rj-name { font-family: var(--mono); font-size: 14px; color: var(--up-ink); }
.rj-date { font-family: var(--mono); font-size: 11.5px; color: var(--ink-3); }
.rj-body { font-size: 13px; line-height: 1.85; color: var(--ink-2); }
.table-foot { font-family: var(--mono); font-size: 11.5px; line-height: 1.9; color: var(--ink-3); margin: 16px 0 0; }

/* ── 结果面板 ── */
.panel { border-top: 1px solid var(--ink); margin-bottom: 8px; }
.panel-head { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; padding: 16px 0; border-bottom: 1px solid var(--rule); }
.panel-head h3 { font-family: var(--mono); font-weight: 600; font-size: 15px; margin: 0; }
.tag { font-family: var(--mono); font-size: 11px; letter-spacing: 1.4px; }
.meta { font-family: var(--mono); font-size: 11px; color: var(--ink-3); }
.stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-bottom: 1px solid var(--rule); background: var(--surface); }
.stat { padding: 22px 24px; border-right: 1px solid var(--rule); display: flex; flex-direction: column; gap: 4px; }
.stat:last-child { border-right: 0; }
.stat-val { font-family: var(--mono); font-weight: 600; font-size: clamp(22px, 2.4vw, 30px); line-height: 1.2; letter-spacing: -1.2px; }
.stat-label { font-family: var(--mono); font-size: 11.5px; color: var(--ink-3); }
.panel-note { font-size: 13px; line-height: 1.9; color: var(--ink-2); margin: 0; padding: 16px 0; }
.panel-note strong { color: var(--ink); font-weight: 600; }

.chart { padding: 32px 24px 20px; background: var(--surface); border-bottom: 1px solid var(--rule); }
.chart svg { width: 100%; height: auto; display: block; overflow: visible; }
.axis { stroke: var(--ink-3); stroke-width: 1; stroke-dasharray: 3 4; opacity: .6; }
.axis-label { font: 500 11px var(--mono); fill: var(--ink-3); }
.bar-up { fill: var(--up); }
.bar-down { fill: var(--down); }
.bar-val { font: 600 18px var(--mono); text-anchor: middle; }
.bar-val.up { fill: var(--up); }
.bar-val.down { fill: var(--down); }
.bar-year { font: 500 14px var(--mono); fill: var(--ink-3); text-anchor: middle; }
.chart-cap { font-family: var(--mono); font-size: 12px; color: var(--ink-3); text-align: center; margin: 12px 0 0; }

.caveats { display: grid; grid-template-columns: 220px minmax(0, 1fr); border-top: 1px solid var(--ink); margin-top: 8px; }
.caveats-side { padding: 20px 28px 24px 0; border-right: 1px solid var(--rule); }
.caveats-side .cap { font-family: var(--mono); font-size: 11px; letter-spacing: 1.4px; }
.caveats-side h3 { font-family: var(--mono); font-weight: 600; font-size: 15px; margin: 8px 0 0; line-height: 1.6; }
.caveat {
  display: grid; grid-template-columns: 36px minmax(0, 1fr); gap: 12px;
  padding: 12px 0 12px 24px; border-bottom: 1px solid var(--rule-soft);
  font-size: 13px; line-height: 1.8; color: var(--ink-2);
}
.caveat:last-child { border-bottom: 0; }
.caveat .rn { font-family: var(--mono); font-size: 11px; color: var(--up); }

/* ── 产品：窗口 + 画廊 ── */
/* min-width: 0 是必需的：grid 子项默认 min-width:auto，示意屏里那几张密排表格的
   min-content 会把窗口撑得比版心还宽，整页因此横向溢出。 */
.windows { display: grid; gap: 26px; }
.win, .card { border: 1px solid var(--rule); background: var(--surface); min-width: 0; }
.win-body > *, .media, .card-media { min-width: 0; }
.win-head {
  display: flex; align-items: center; gap: 10px; padding: 11px 18px;
  border-bottom: 1px solid var(--rule); background: var(--paper-2);
  font-family: var(--mono); font-size: 11px; letter-spacing: 1.2px; color: var(--ink-3);
}
.win-head b { color: var(--ink); font-weight: 500; }
.win-body { display: grid; }
.win-body.media-left { grid-template-columns: minmax(0, 1fr) 300px; }
.win-body.media-right { grid-template-columns: 300px minmax(0, 1fr); }
.media { padding: 18px; }
.win-body.media-left .media { border-right: 1px solid var(--rule); }
.win-body.media-right .media { border-left: 1px solid var(--rule); }
.copy { padding: 22px 24px; }
.copy h3 { font-family: var(--mono); font-weight: 600; font-size: 16px; margin: 0 0 12px; }
.copy p { font-size: 13.5px; line-height: 1.9; color: var(--ink-2); margin: 0 0 16px; }
.ticks { list-style: none; margin: 0; padding: 0; display: grid; }
.ticks li { font-size: 12.5px; line-height: 1.75; color: var(--ink-2); padding: 9px 0; border-top: 1px solid var(--rule-soft); }
.ticks.muted li { color: var(--ink-3); }

.gallery-head {
  display: flex; align-items: baseline; gap: 14px; padding: 12px 0; margin-top: 36px;
  border-top: 1px solid var(--ink); border-bottom: 1px solid var(--rule);
}
.gallery-head .cap { font-family: var(--mono); font-size: 11px; letter-spacing: 1.6px; color: var(--ink-3); }
.gallery { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 26px; padding-top: 26px; }
.card { display: flex; flex-direction: column; }
.card-media { padding: 18px; border-bottom: 1px solid var(--rule-soft); }
.card-copy { padding: 18px 20px 20px; }
.card-copy.bordered { border-bottom: 1px solid var(--rule-soft); }
.card-copy h3 { font-family: var(--mono); font-weight: 600; font-size: 15px; margin: 0 0 12px; }

/* ── 示意屏通用 ── */
.screen { background: #fff; border: 1px solid var(--rule); padding: 14px 16px; }
.screen-say { font-size: 11.5px; line-height: 1.75; color: var(--ink-2); margin: 10px 0 0; }
.screen-say.bordered { margin-top: 11px; padding-top: 10px; border-top: 1px solid var(--rule-soft); }
.screen-say b { color: var(--ink); font-weight: 600; }
.screen-foot { font-size: 11.5px; line-height: 1.8; color: var(--ink-3); margin: 12px 0 0; }
.screen .k { font-family: var(--mono); font-size: 10.5px; letter-spacing: 1.2px; color: var(--ink-3); }
.screen .v { font-family: var(--mono); font-weight: 700; font-size: 22px; line-height: 1; }
.screen .m { font-family: var(--mono); font-size: 11px; color: var(--ink-2); }
.chip { font-family: var(--mono); font-size: 10.5px; color: var(--ink-3); border: 1px solid var(--rule); padding: 3px 9px; }
.chip.solid { background: var(--up); color: #fff; border-color: var(--up); }
.chip.down { color: var(--down); }
.chip.warn { color: var(--warn); border-color: var(--warn); }

.ov-top, .ov-risk, .au-top, .st-top { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.ov-top { padding-bottom: 12px; border-bottom: 1px solid var(--rule); }
.ov-risk { padding: 12px 0; border-bottom: 1px solid var(--rule); }
.tiers { display: flex; gap: 3px; flex-grow: 1; max-width: 300px; }
.tiers i { flex-grow: 1; height: 7px; }
.tiers.wide { max-width: none; margin: 14px 0 8px; }
.tiers .t-safe { background: var(--down); opacity: .35; }
.tiers .t-warn { background: var(--warn); }
.tiers .t-danger { background: var(--up); opacity: .35; }
.tiers .t-crit { background: var(--up-ink); opacity: .35; }
.tier-labels { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 4px; font-family: var(--mono); font-size: 10px; color: var(--ink-3); }
.ov-risk .say { font-size: 11.5px; color: var(--ink-2); }

.ov-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; padding-top: 12px; }
.mini { border: 1px solid var(--rule-soft); background: #fff; padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.mini-head { display: flex; align-items: baseline; gap: 8px; font-family: var(--mono); font-size: 10.5px; color: var(--ink-3); }
.mini-head b { color: var(--ink); font-weight: 600; font-size: 11.5px; }
.kvline { display: flex; justify-content: space-between; font-family: var(--mono); font-size: 10.5px; padding: 3px 0; }
.tri { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 5px; }
.tri.wide { gap: 6px; margin-top: 13px; }
.tri span { border: 1px solid var(--rule-soft); background: var(--paper); padding: 6px 4px; text-align: center; }
.tri.wide span { padding: 7px 4px; }
.tri b { display: block; font-family: var(--mono); font-size: 17px; font-weight: 700; line-height: 1.2; }
.tri i { font-style: normal; font-size: 9.5px; color: var(--ink-3); }
.barline { display: flex; align-items: center; gap: 6px; padding: 3px 0; font-family: var(--mono); font-size: 10px; }
.bl-name { flex: 0 0 62px; color: var(--ink-2); }
.bl-name.wide { flex: 0 0 104px; font-family: system-ui, sans-serif; font-size: 10.5px; }
.bl-track { flex-grow: 1; height: 6px; background: var(--rule-soft); }
.bl-track i { display: block; height: 6px; }
.bl-track i.up { background: var(--up); }
.bl-track i.down { background: var(--down); }
.bl-val { flex: 0 0 46px; text-align: right; font-weight: 600; }
.st-dims .bl-val { flex: 0 0 62px; }
.mini-tbl { display: grid; grid-template-columns: 54px repeat(3, minmax(0, 1fr)); gap: 2px 6px; font-family: var(--mono); font-size: 10px; }

.sm-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding-bottom: 12px; border-bottom: 1px solid var(--rule); }
.sm-bar b { font-family: var(--mono); font-size: 12px; font-weight: 600; }
.sm-sub { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; padding: 11px 0; font-family: var(--mono); }
.sm-sub b { font-size: 13px; font-weight: 700; }
.sm-sub .dim { font-size: 10.5px; }
.sm-head, .sm-row {
  display: grid;
  grid-template-columns: 76px 60px 104px 64px 82px minmax(0, 1fr) 56px 66px 118px;
  gap: 10px;
}
.sm-head { padding: 8px 0; border-top: 1px solid var(--ink); border-bottom: 1px solid var(--rule); font-family: var(--mono); font-size: 10px; letter-spacing: .8px; color: var(--ink-3); }
.sm-row { padding: 10px 0; border-bottom: 1px solid var(--rule-soft); align-items: center; font-size: 12.5px; }
.sm-row .rank { font-family: var(--mono); font-weight: 700; font-size: 13px; }
.sm-row .mono { font-size: 12px; }
.sm-row .timing { font-family: var(--mono); font-size: 10px; padding: 2px 6px; justify-self: start; border: 1px solid currentColor; }
.sm-row .timing.ok { color: var(--up); }
.sm-row .timing.wait { color: var(--ink-3); }
.sm-row .ref { font-family: var(--mono); font-size: 10px; color: var(--ink-3); line-height: 1.6; }

.rv-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding-bottom: 12px; border-bottom: 1px solid var(--rule); }
.rv-bar b { font-family: var(--mono); font-size: 12px; font-weight: 600; }
.rv-bar .say { font-size: 11.5px; color: var(--ink-2); }
.rv-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; padding-top: 12px; }
.rv-pool { border: 1px solid var(--rule-soft); background: #fff; padding: 12px; }
.rv-pool-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; font-family: var(--mono); }
.rv-pool-head b { font-size: 12.5px; font-weight: 600; }
.rv-pool-head .dim { font-size: 10px; }
.rv-cells { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
.rv-cell { border: 1px solid var(--rule-soft); background: var(--paper); padding: 9px 8px; text-align: center; }
.rv-cell b { display: block; font-family: var(--mono); font-size: 19px; font-weight: 700; line-height: 1.25; }
.rv-cell i { display: block; font-style: normal; font-family: var(--mono); font-size: 9.5px; color: var(--ink-3); line-height: 1.7; }

.au-top { padding-bottom: 11px; border-bottom: 1px solid var(--rule); }
.au-kv { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 20px; font-family: var(--mono); font-size: 11.5px; }
.au-kv > span { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--rule-soft); }
.au-dist { display: flex; align-items: center; gap: 8px; padding-top: 11px; font-family: var(--mono); font-size: 10px; }
.dist { display: flex; flex-grow: 1; height: 8px; }
.dist i.up { background: var(--up); }
.dist i.down { background: var(--down); }
.dist i.flat { background: var(--rule); }

.tm-top { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; padding-bottom: 10px; font-family: var(--mono); font-size: 10px; }
.tm-top b { font-size: 12px; font-weight: 600; }
.treemap { display: flex; flex-direction: column; background: #22262c; }
.tm-row { display: flex; }
.tm-cell {
  flex-basis: 0; color: #fff; display: flex; flex-direction: column;
  align-items: center; justify-content: center; padding: 2px; overflow: hidden;
  border: 1px solid rgba(0, 0, 0, .28);
}
.tm-cell b { font-family: var(--mono); font-size: 9.5px; font-weight: 500; line-height: 1.3; white-space: nowrap; }
.tm-cell i { font-style: normal; font-family: var(--mono); font-size: 9px; opacity: .82; line-height: 1.3; }

.lu-stats { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 6px; }
.lu-stat { border: 1px solid var(--rule-soft); border-top: 2px solid; background: var(--paper); padding: 8px 4px; text-align: center; }
.lu-stat b { display: block; font-family: var(--mono); font-size: 20px; font-weight: 700; line-height: 1.2; }
.lu-stat i { font-style: normal; font-size: 9.5px; color: var(--ink-3); }
.lu-tags { display: flex; flex-wrap: wrap; gap: 5px; padding: 12px 0 11px; }
.lu-tags span { font-family: var(--mono); font-size: 9.5px; border: 1px solid var(--rule); padding: 2px 7px; color: var(--ink-2); }
.lu-tags b { color: var(--up); }
.lu-row {
  display: grid; grid-template-columns: 56px 40px minmax(0, 1fr); gap: 10px;
  align-items: baseline; padding: 7px 0; border-top: 1px solid var(--rule-soft); font-size: 11px;
}
.lu-tier { font-family: var(--mono); font-size: 10px; color: #fff; padding: 2px 0; text-align: center; }
.lu-row .dim { font-family: var(--mono); font-size: 10px; }

.st-top { padding-bottom: 11px; border-bottom: 1px solid var(--rule); }
.st-top .name { font-size: 15px; font-weight: 700; }
.st-top .price { font-family: var(--mono); font-size: 17px; font-weight: 700; }
.st-top .dim { font-family: var(--mono); font-size: 10.5px; }
.st-dims { display: grid; padding: 10px 0 12px; }
.st-chart { border-top: 1px solid var(--rule-soft); padding-top: 10px; }
.st-chart-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 6px; font-family: var(--mono); font-size: 10px; color: var(--ink-3); }
.st-chart svg { width: 100%; height: auto; display: block; }
.ma { font-size: 9.5px; }
.ma5 { color: #d9a441; }
.ma10 { color: #3b7dd8; }
.ma20 { color: #8b5cc7; }
.k-grid { stroke: var(--rule-soft); stroke-width: 1; }
.k-axis { font: 500 10px var(--mono); fill: #a49c90; text-anchor: end; }
.k-axis.mid { text-anchor: middle; }
.k-up, .k-up-s { fill: var(--up); stroke: var(--up); stroke-width: 1.2; }
.k-down, .k-down-s { fill: var(--down); stroke: var(--down); stroke-width: 1.2; }
.k-ma5, .k-ma10, .k-ma20 { fill: none; stroke-width: 1.6; stroke-linejoin: round; }
.k-ma5 { stroke: #d9a441; }
.k-ma10 { stroke: #3b7dd8; }
.k-ma20 { stroke: #8b5cc7; }

/* ── 交易日时间线 ── */
.timeline { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); border-top: 2px solid var(--ink); }
.tl-step { padding: 18px 22px 26px; border-right: 1px solid var(--rule); }
.tl-step:first-child { padding-left: 0; }
.tl-step:last-child { padding-right: 0; border-right: 0; }
.tl-at { font-family: var(--mono); font-size: 12px; font-weight: 600; letter-spacing: 1.2px; color: var(--up); }
.tl-step h3 { font-family: var(--mono); font-weight: 600; font-size: 15px; margin: 8px 0; }
.tl-step p { font-size: 12.5px; line-height: 1.85; color: var(--ink-2); margin: 0; }

/* ── 适合谁 ── */
.fit-yes, .fit-no { font-family: var(--mono); font-weight: 600; font-size: 15px; margin: 0 0 12px; }
.fit-no { color: var(--ink-3); }

/* ── 定价 ── */
.pricing { display: grid; grid-template-columns: minmax(0, 1fr) 360px; }
.pricing .col { padding: 26px 32px 30px 0; border-right: 1px solid var(--rule); }
.pricing .col:last-child { padding: 0 0 0 32px; border-right: 0; }
.free-lead { font-size: 17px; line-height: 1.8; margin: 0 0 14px; font-weight: 600; }
.free-lead strong { color: var(--up-ink); }
.pricing p { font-size: 14px; line-height: 1.9; color: var(--ink-2); margin: 0 0 12px; }
.pricing .btn { margin-top: 12px; }
.cap.boxed { padding: 12px 0; border-bottom: 1px solid var(--rule); font-family: var(--mono); font-size: 10.5px; letter-spacing: 1.6px; color: var(--ink-3); }
.pricing .ticks li { border-top: 0; border-bottom: 1px solid var(--rule-soft); padding: 12px 0; }

/* ── FAQ ── */
.faq { border-top: 1px solid var(--ink); }
.faq-row {
  display: grid; grid-template-columns: 340px minmax(0, 1fr); gap: 32px;
  padding: 18px 0; border-bottom: 1px solid var(--rule-soft); align-items: baseline;
}
.faq-row:last-child { border-bottom: 0; }
.faq-row h3 { font-family: var(--mono); font-weight: 600; font-size: 14.5px; margin: 0; }
.faq-row p { font-size: 13.5px; line-height: 1.9; color: var(--ink-2); margin: 0; }
.faq-row p :deep(strong) { color: var(--ink); font-weight: 600; }

/* ── 页脚 ── */
.foot { background: var(--ink); color: #b3aca1; font-size: 12.5px; line-height: 1.9; }
.foot .wrap { padding-top: 36px; padding-bottom: 40px; }
.foot-brand { display: flex; align-items: center; gap: 10px; margin-bottom: 18px; }
.foot-brand span { font-family: var(--mono); font-weight: 700; font-size: 14px; letter-spacing: .5px; color: #fff; }
.disclaimer { max-width: 60em; margin: 0 0 20px; }
.foot-links { display: flex; gap: 24px; padding-bottom: 18px; border-bottom: 1px solid #34302a; font-family: var(--mono); font-size: 12px; }
.foot-links a { color: #ded7cc; text-decoration: none; }
.foot-links a:hover { color: #fff; }
.copy { margin: 16px 0 0; font-family: var(--mono); font-size: 11.5px; color: #7b7469; }

/* ── 响应式 ──
   1180 以下把「主体 + 侧栏」的分栏拆掉；860 以下整页单列。
   栅格竖线在单列下会变成孤立的一竖，所以一并去掉，只留横线。 */
@media (max-width: 1180px) {
  .wrap { padding: 0 28px; }
  .hero-grid { grid-template-columns: 1fr; }
  .hero-copy { padding: 44px 0 32px; }
  .hero-panel { padding: 22px 0 40px; border-left: 0; border-top: 1px solid var(--rule); }
  .win-body.media-left, .win-body.media-right { grid-template-columns: 1fr; }
  .win-body.media-right .copy { order: 2; }
  .win-body.media-right .media { order: 1; border-left: 0; border-bottom: 1px solid var(--rule); }
  .win-body.media-left .media { border-right: 0; border-bottom: 1px solid var(--rule); }
  .gallery { grid-template-columns: 1fr; }
  .faq-row { grid-template-columns: 1fr; gap: 8px; }
  .pricing { grid-template-columns: 1fr; }
  .pricing .col { padding: 26px 0; border-right: 0; border-bottom: 1px solid var(--rule); }
  .pricing .col:last-child { padding: 20px 0 0; border-bottom: 0; }
  .timeline { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .tl-step { padding: 18px 20px 24px; border-bottom: 1px solid var(--rule); }
  .tl-step:nth-child(2n) { padding-right: 0; border-right: 0; }
  .tl-step:nth-child(2n + 1) { padding-left: 0; }
  .sm-head, .sm-row { grid-template-columns: 60px 52px 92px 60px 74px minmax(0, 1fr) 52px 60px; }
  .sm-head span:last-child, .sm-row .ref { display: none; }
}

@media (max-width: 860px) {
  .wrap { padding: 0 18px; }
  .links { display: none; }
  .ticker-inner { flex-wrap: wrap; }
  .ticker-cell { flex: 1 0 45%; border-right: 0; padding: 6px 0; }
  .evidence-grid { grid-template-columns: 1fr; }
  .evidence-cell, .evidence-cell:last-child { padding: 24px 0; border-right: 0; }
  .evidence-cell:first-child { border-bottom: 1px solid var(--rule); }
  .cols-3, .cols-2 { grid-template-columns: 1fr; }
  .ruled .col { padding: 22px 0 24px; border-right: 0; border-bottom: 1px solid var(--rule); }
  .ruled .col:last-child { border-bottom: 0; }
  .tr, .reject.table .tr { grid-template-columns: 1fr; gap: 6px; }
  .tr.th { display: none; }
  .gate-name em { display: inline; margin-left: 8px; }
  .passed { grid-template-columns: 1fr; gap: 14px; }
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stat:nth-child(2n) { border-right: 0; }
  .stat:nth-child(-n + 2) { border-bottom: 1px solid var(--rule); }
  .caveats { grid-template-columns: 1fr; }
  .caveats-side { padding: 18px 0 16px; border-right: 0; border-bottom: 1px solid var(--rule); }
  .caveat { padding-left: 0; }
  .ov-grid, .rv-grid { grid-template-columns: 1fr; }
  .lu-stats { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .au-kv { grid-template-columns: 1fr; }
  /* 密排的示意屏在手机上不硬压：让它在自己的容器里横向滚，
     页面主体绝不横向滚动。 */
  .media, .card-media { overflow-x: auto; }
  .media > .screen, .card-media > .screen { min-width: 300px; }
  .timeline { grid-template-columns: 1fr; }
  .tl-step, .tl-step:nth-child(2n) { padding: 16px 0 20px; border-right: 0; }
  .sm-head, .sm-row { grid-template-columns: 56px 84px minmax(0, 1fr) 56px 60px; }
  .sm-head span:nth-child(2), .sm-head span:nth-child(4), .sm-head span:nth-child(6) { display: none; }
  .sm-row > span:nth-child(2), .sm-row > span:nth-child(4), .sm-row > span:nth-child(6) { display: none; }
  /* SVG 文字跟着 viewBox 等比缩，窄屏下按用户单位加大 */
  .bar-val { font-size: 22px; }
  .bar-year { font-size: 17px; }
  .chart { padding: 24px 8px 16px; }
}
</style>
