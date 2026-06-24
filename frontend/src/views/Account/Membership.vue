<template>
  <div class="membership">
    <div class="page-head">
      <h2>会员与用量</h2>
      <p>查看今日 AI 配额、开通会员，并绑定会员专属微信提醒。</p>
    </div>

    <el-card v-if="info" class="card">
      <div class="plan-line">
        <el-tag :type="info.plan === 'member' ? 'warning' : 'info'" effect="dark" size="large">
          {{ info.plan_label }}
        </el-tag>
        <span v-if="info.plan_expires_at" class="expires">有效期至 {{ info.plan_expires_at }}</span>
      </div>
      <div class="usage">
        <span>今日 AI 分析额度</span>
        <el-progress
          :percentage="info.daily_limit ? Math.min(100, (info.used_today / info.daily_limit) * 100) : 0"
          :format="() => usageLabel"
        />
      </div>
    </el-card>

    <el-card v-if="info && info.plan !== 'member'" class="card upgrade">
      <h3>升级会员</h3>
      <ul class="benefits">
        <li>每日 AI 深度分析 3 次提升到 30 次</li>
        <li>解锁催化剂深度报告和实验室功能</li>
        <li>支持自选股价格预警与催化剂命中微信提醒</li>
      </ul>
      <el-alert type="info" :closable="false" :title="upgrade?.instructions || defaultUpgradeText" />
      <div class="upgrade-info">
        <div>
          <span>会员方案</span>
          <b>{{ upgrade?.price_text || '内测会员：人工确认后开通' }}</b>
        </div>
        <div>
          <span>开通方式</span>
          <b>{{ upgrade?.wechat_id || '联系管理员开通' }}</b>
        </div>
      </div>
      <img v-if="upgrade?.qr_url" class="pay-qr" :src="upgrade.qr_url" alt="会员开通二维码" />
      <el-alert
        v-if="upgrade && !upgrade.configured"
        type="warning"
        :closable="false"
        title="当前环境未配置运营微信或收款二维码。部署时设置 LYNX_MEMBERSHIP_WECHAT / LYNX_MEMBERSHIP_QR_URL 即可展示真实开通信息。"
      />
    </el-card>

    <el-card v-if="runtime" class="card">
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

    <el-card class="card">
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
        title="微信推送为会员专属功能。可先绑定，升级会员后自动生效。"
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
import {
  fetchBillingMe,
  fetchRuntimeValidation,
  fetchUpgradeInfo,
  type BillingMe,
  type RuntimeConfigValidation,
  type UpgradeInfo,
} from '@/api/billing'
import {
  bindWechatPush,
  fetchWechatStatus,
  testWechatPush,
  unbindWechatPush,
  type WechatPushStatus,
} from '@/api/notifications'

const info = ref<BillingMe | null>(null)
const upgrade = ref<UpgradeInfo | null>(null)
const runtime = ref<RuntimeConfigValidation | null>(null)
const wechatStatus = ref<WechatPushStatus | null>(null)
const savingPush = ref(false)
const testingPush = ref(false)
const defaultUpgradeText = '添加运营微信并备注注册用户名，付款确认后由管理员开通会员。'
const pushForm = reactive({
  serverchan_key: '',
  pushplus_token: '',
  enabled: true,
})

const usageLabel = computed(() => info.value ? `${info.value.used_today}/${info.value.daily_limit}` : '')

async function loadPage() {
  try {
    const [billingRes, upgradeRes, pushRes, runtimeRes] = await Promise.all([
      fetchBillingMe(),
      fetchUpgradeInfo(),
      fetchWechatStatus(),
      fetchRuntimeValidation(),
    ])
    info.value = (billingRes?.data as BillingMe) ?? null
    upgrade.value = (upgradeRes?.data as UpgradeInfo) ?? null
    wechatStatus.value = (pushRes?.data as WechatPushStatus) ?? null
    runtime.value = (runtimeRes?.data as RuntimeConfigValidation) ?? null
  } catch (e: any) {
    ElMessage.error(e?.message || '会员信息加载失败')
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
.upgrade-info, .bound-box {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin: 14px 0;
}
.upgrade-info div, .bound-box div {
  background: var(--el-fill-color-lighter);
  border-radius: 6px;
  padding: 10px 12px;
}
.upgrade-info span, .bound-box span {
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
  .upgrade-info, .bound-box { grid-template-columns: 1fr; }
}
</style>
