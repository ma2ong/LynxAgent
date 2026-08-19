<template>
  <div class="membership">
    <div class="page-head">
      <h2>用量</h2>
      <p>查看今日 AI 分析配额的使用情况。</p>
    </div>

    <el-card v-if="info" class="card">
      <div class="plan-line">
        <el-tag :type="info.plan === 'member' ? 'warning' : 'info'" effect="dark" size="large">
          {{ info.plan_label }}
        </el-tag>
        <span v-if="info.plan_expires_at" class="expires">有效期至 {{ info.plan_expires_at }}</span>
      </div>
      <div class="usage">
        <span>今日 AI 分析</span>
        <template v-if="info.unlimited">
          <p class="usage-plain">
            <b>{{ info.used_today }}</b> 次 · 不限次数
            <em v-if="!info.ai_enabled">（AI 功能当前未开启）</em>
          </p>
        </template>
        <el-progress
          v-else
          :percentage="info.daily_limit ? Math.min(100, (info.used_today / info.daily_limit) * 100) : 0"
          :format="() => usageLabel"
        />
      </div>
    </el-card>

    <!-- 付费档已下线（2026-08-18）。本产品无证券投资咨询资质，收费会改变「提供个股名单」
         这件事的性质，所以不设付费档 —— 这是合规决定，不是定价策略。
         后端 PLANS 里的 member 档保留不动：已有账号的额度不受影响，只是不再对外销售。
         原来这里是支付宝收款码 + 开通申请单，整块移除。 -->
    <el-card v-if="info && info.plan !== 'member'" class="card">
      <h3>关于额度与 AI</h3>
      <p class="free-note">
        本产品全部功能免费，没有付费档，每日分析也不限次数。选股池、名单、回放数据和
        复盘战绩都完整开放。
      </p>
      <p class="free-note dim">
        涉及 AI 模型的功能（个股深研的深度分析、五方判读、事件驱动）当前未开启。
        其余内容全部由本地规则计算，不依赖 AI，不受影响。
      </p>
    </el-card>

    <!-- 只在「还有必填项没配」时出现。全绿的清单是噪音，天天占着版面提醒你一切正常。
         2026-08-06 同时删掉了三条过时检查（运营微信 / ICP备案 / 微信推送 token）。 -->
    <el-card v-if="runtime && !runtime.valid" class="card">
      <div class="card-title">
        <h3>上线配置检查</h3>
        <el-tag size="small" :type="runtime.valid ? 'success' : 'warning'">
          {{ runtime.valid ? '关键项已配置' : '还有关键项未配置' }}
        </el-tag>
      </div>
      <div class="config-list">
        <div v-for="item in runtime.checks" :key="item.key" class="config-row">
          <div>
            <b>{{ item.label }}</b>
            <span>{{ item.message }}</span>
          </div>
          <el-tag size="small" :type="item.ok ? 'success' : item.required ? 'danger' : 'warning'" effect="plain">
            {{ item.ok ? '已配置' : item.required ? '上线前必填' : '建议配置' }}
          </el-tag>
        </div>
      </div>
    </el-card>

    <!-- 微信推送整块暂时隐藏（2026-08-06）：产品要去掉对微信的依赖，但推送渠道换成什么
         还没定，代码先留着不删，免得将来重做。想放回来把 v-if 去掉即可。 -->
    <el-card v-if="false" class="card">
      <div class="card-title">
        <h3>微信推送</h3>
        <el-tag size="small" :type="wechatStatus?.bound ? 'success' : 'info'">
          {{ wechatStatus?.bound ? '已绑定' : '未绑定' }}
        </el-tag>
      </div>
      <p class="muted">会员绑定后，自选股触发价格预警或命中催化剂事件时，会同步发送微信提醒。</p>

      <el-alert
        v-if="wechatStatus && !wechatStatus.member_push_allowed"
        type="warning"
        :closable="false"
        title="微信推送渠道正在调整，暂未开放。"
      />

      <div v-if="wechatStatus?.bound" class="bound-box">
        <div><span>Server酱</span><b>{{ wechatStatus.serverchan_key_masked || '-' }}</b></div>
        <div><span>PushPlus</span><b>{{ wechatStatus.pushplus_token_masked || '-' }}</b></div>
        <div><span>更新时间</span><b>{{ (wechatStatus.updated_at || '').slice(0, 16) || '-' }}</b></div>
      </div>

      <el-form label-position="top" class="push-form">
        <el-form-item label="Server酱 SendKey">
          <el-input v-model="pushForm.serverchan_key" placeholder="SCT...，二选一即可" show-password />
        </el-form-item>
        <el-form-item label="PushPlus Token">
          <el-input v-model="pushForm.pushplus_token" placeholder="PushPlus token，二选一即可" show-password />
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="pushForm.enabled">启用微信推送</el-checkbox>
        </el-form-item>
      </el-form>

      <div class="actions">
        <el-button type="primary" :loading="savingPush" @click="saveWechat">保存绑定</el-button>
        <el-button :disabled="!wechatStatus?.bound" :loading="testingPush" @click="sendTest">发送测试</el-button>
        <el-button v-if="wechatStatus?.bound" type="danger" plain @click="unbindWechat">解绑</el-button>
      </div>
    </el-card>

    <p class="disclaimer">
      本产品为 AI 研究工具，所有内容仅供研究参考，不构成投资建议。市场有风险，决策需独立。
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiClient } from '@/api/request'
import {
  fetchBillingMe,
  fetchRuntimeValidation,
  type BillingMe,
  type RuntimeConfigValidation,
} from '@/api/billing'
import {
  bindWechatPush,
  fetchWechatStatus,
  testWechatPush,
  unbindWechatPush,
  type WechatPushStatus,
} from '@/api/notifications'

const info = ref<BillingMe | null>(null)
const runtime = ref<RuntimeConfigValidation | null>(null)
const wechatStatus = ref<WechatPushStatus | null>(null)
const savingPush = ref(false)
const testingPush = ref(false)

const pushForm = reactive({
  serverchan_key: '',
  pushplus_token: '',
  enabled: true,
})

const usageLabel = computed(() => info.value ? `${info.value.used_today}/${info.value.daily_limit}` : '')

async function loadPage() {
  try {
    const [billingRes, pushRes, runtimeRes] = await Promise.all([
      fetchBillingMe(),
      fetchWechatStatus(),
      fetchRuntimeValidation(),
    ])
    info.value = (billingRes?.data as BillingMe) ?? null
    wechatStatus.value = (pushRes?.data as WechatPushStatus) ?? null
    runtime.value = (runtimeRes?.data as RuntimeConfigValidation) ?? null
  } catch (e: any) {
    ElMessage.error(e?.message || '用量信息加载失败')
  }
}

async function saveWechat() {
  if (!pushForm.serverchan_key.trim() && !pushForm.pushplus_token.trim()) {
    ElMessage.warning('请填写 Server酱 SendKey 或 PushPlus Token')
    return
  }
  savingPush.value = true
  try {
    const res = await bindWechatPush({
      serverchan_key: pushForm.serverchan_key.trim() || null,
      pushplus_token: pushForm.pushplus_token.trim() || null,
      enabled: pushForm.enabled,
    })
    wechatStatus.value = (res?.data as WechatPushStatus) ?? null
    pushForm.serverchan_key = ''
    pushForm.pushplus_token = ''
    ElMessage.success('微信推送已绑定')
  } catch (e: any) {
    ElMessage.error(e?.message || '绑定失败')
  } finally {
    savingPush.value = false
  }
}

async function sendTest() {
  testingPush.value = true
  try {
    await testWechatPush()
    ElMessage.success('测试通知已发送')
    await loadPage()
  } catch (e: any) {
    ElMessage.error(e?.message || '测试发送失败')
  } finally {
    testingPush.value = false
  }
}

async function unbindWechat() {
  try {
    const res = await unbindWechatPush()
    wechatStatus.value = (res?.data as WechatPushStatus) ?? null
    ElMessage.success('已解绑微信推送')
  } catch (e: any) {
    ElMessage.error(e?.message || '解绑失败')
  }
}

onMounted(loadPage)
</script>

<style scoped lang="scss">
.membership { max-width: 820px; margin: 0 auto; padding: 16px; }
.page-head { margin-bottom: 16px; }
.page-head h2 { margin: 0 0 4px; font-size: 22px; }
.page-head p { margin: 0; color: var(--el-text-color-secondary); }
.card { margin-bottom: 16px; border-radius: 8px; }
.plan-line { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.expires { color: #909399; font-size: 13px; }
.usage { display: flex; flex-direction: column; gap: 8px; font-size: 14px; color: #606266; }
.benefits { margin: 8px 0 16px; padding-left: 20px; color: #606266; line-height: 1.9; }
.bound-box {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin: 14px 0;
}
.bound-box div {
  background: var(--el-fill-color-lighter);
  border-radius: 6px;
  padding: 10px 12px;
}
.bound-box span {
  display: block;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}
.pay-qr { width: 180px; height: 180px; object-fit: contain; border: 1px solid var(--el-border-color-light); border-radius: 8px; }
.card-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.card-title h3 { margin: 0; }
.muted { color: var(--el-text-color-secondary); line-height: 1.7; }
.push-form { margin-top: 14px; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; }
.disclaimer { color: #909399; font-size: 12px; text-align: center; line-height: 1.6; }

@media (max-width: 720px) {
  .bound-box { grid-template-columns: 1fr; }
}
</style>
