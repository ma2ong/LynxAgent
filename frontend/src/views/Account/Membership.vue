<template>
  <div class="membership">
    <div class="page-head">
      <h2>用户设置</h2>
      <p>配置你自己的 AI API Key、查看今日调用次数，以及退出登录。</p>
    </div>

    <!-- BYOK：用户自带密钥。站点统一配一把的话，任何注册用户都能改服务端配置，
         那是个权限洞；而且产品不收费，推理成本不该由站长垫。 -->
    <el-card class="card">
      <div class="card-title">
        <h3>AI 功能（自带 API Key）</h3>
        <el-tag size="small" :type="keyMeta ? 'success' : 'info'">
          {{ keyMeta ? '已配置' : '未配置' }}
        </el-tag>
      </div>

      <div v-if="keyMeta" class="key-bound">
        <div><span>服务商</span><b>{{ providerLabel(keyMeta.provider) }}</b></div>
        <div><span>模型</span><b>{{ keyMeta.model }}</b></div>
        <div><span>密钥</span><b>••••••••{{ keyMeta.key_tail }}</b></div>
        <div><span>更新于</span><b>{{ (keyMeta.updated_at || '').slice(0, 16).replace('T', ' ') }}</b></div>
      </div>

      <el-form label-position="top" class="key-form" @submit.prevent>
        <el-form-item label="服务商">
          <el-select v-model="keyForm.provider" style="width: 100%" @change="onProviderChange">
            <el-option v-for="p in providers" :key="p.key" :label="p.label" :value="p.key" />
          </el-select>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="keyForm.api_key" type="password" show-password clearable
                    placeholder="粘贴你自己的密钥，保存后不再回显" />
        </el-form-item>
        <el-form-item label="接口地址">
          <el-input v-model="keyForm.base_url" placeholder="https://api.deepseek.com" clearable />
        </el-form-item>
        <el-form-item label="模型名">
          <el-input v-model="keyForm.model" placeholder="deepseek-chat" clearable />
        </el-form-item>
        <div class="key-actions">
          <el-button :loading="keyTesting" @click="testKey">测试连接</el-button>
          <el-button type="primary" :loading="keySaving" @click="saveKey">保存</el-button>
          <el-button v-if="keyMeta" type="danger" plain :loading="keyDeleting" @click="deleteKey">删除</el-button>
        </div>
      </el-form>

      <p class="free-note dim">
        密钥加密后存在本机数据库，接口从不回传明文。费用由你的服务商账户结算，本站不经手。
        不填也不影响使用：页面上其余内容全部由本地规则计算。
      </p>
    </el-card>

    <!-- 用量降为一行：额度已取消，这里只剩「今天调了几次」这一个事实，
         不值得再占一整张卡片。 -->
    <el-card v-if="info" class="card">
      <h3>用量与说明</h3>
      <p class="usage-plain">
        今日 AI 调用 <b>{{ info.used_today }}</b> 次 · 不限次数
        <em v-if="!info.ai_enabled">（未配置 API Key，AI 功能未启用）</em>
      </p>
      <p class="free-note">
        本产品全部功能免费，没有付费档。选股池、名单、回放数据和复盘战绩都完整开放，
        且全部由本地规则计算，不依赖 AI。
      </p>
      <p class="free-note dim">
        只有个股深研的「深度多智能体分析」需要 AI 模型，那一项才用得上上面配置的密钥。
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

    <!-- 用户管理并入本页（2026-08-28）：它原本是侧栏一个独立菜单，和「用户设置」
         并排放着，两个都叫「用户」，管理员之外的人还看不到——重复且占位。
         放在「账号」之前：先管别人，再管自己（退出登录是整页最后一步）。 -->
    <el-card v-if="currentUser?.is_admin" class="card">
      <div class="card-title">
        <h3>用户管理</h3>
        <span class="card-hint">全站账号的套餐与启用状态，仅管理员可见</span>
      </div>
      <AdminUsers />
    </el-card>

    <!-- 规则生命周期同样并进本页：它和用户管理一样是仅管理员可见的内部视图，
         再开一条侧栏菜单只会让每天要点的那些入口更挤。放在用户管理之后——
         前者是运营，后者是研究，运营的事更常做。 -->
    <el-card v-if="currentUser?.is_admin" class="card">
      <div class="card-title">
        <h3>规则生命周期</h3>
        <span class="card-hint">每条选股规则审到哪一步、结论是什么，仅管理员可见</span>
      </div>
      <RuleLifecycle />
    </el-card>

    <!-- 退出登录从侧栏底部收到这里：低频且不可撤销，常驻侧栏只会被误点。
         放在整页最后、且要二次确认——避免用户想改 API Key 却顺手把自己登出了。 -->
    <el-card class="card">
      <div class="card-title">
        <h3>账号</h3>
      </div>
      <p class="account-line">
        当前登录 <b>{{ currentUser?.username || currentUser?.email || '—' }}</b>
        <em v-if="currentUser?.is_admin">（管理员）</em>
      </p>
      <el-button type="danger" plain @click="confirmLogout">
        <el-icon><SwitchButton /></el-icon>退出登录
      </el-button>
    </el-card>

    <p class="disclaimer">
      本产品为 AI 研究工具，所有内容仅供研究参考，不构成投资建议。市场有风险，决策需独立。
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { SwitchButton } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { ApiClient } from '@/api/request'
import { currentUser, clearCurrentUser } from '@/stores/user'
import AdminUsers from '@/views/Admin/Users.vue'
import RuleLifecycle from '@/views/Admin/RuleLifecycle.vue'
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

const router = useRouter()

// 二次确认：这个按钮就在 API Key 表单下面，误点一次就得重新登录
const confirmLogout = async () => {
  try {
    await ElMessageBox.confirm('退出后需要重新登录才能使用。确定退出吗？', '退出登录', {
      confirmButtonText: '退出', cancelButtonText: '取消', type: 'warning',
    })
  } catch {
    return  // 用户取消
  }
  clearCurrentUser()
  localStorage.removeItem('auth-token')
  router.push('/login')
}

const info = ref<BillingMe | null>(null)
const runtime = ref<RuntimeConfigValidation | null>(null)
const wechatStatus = ref<WechatPushStatus | null>(null)
const savingPush = ref(false)
const testingPush = ref(false)

// ---- BYOK ----
const providers = ref<any[]>([])
const keyMeta = ref<any>(null)
const keyTesting = ref(false)
const keySaving = ref(false)
const keyDeleting = ref(false)
const keyForm = reactive({ provider: 'deepseek', api_key: '', base_url: '', model: '' })

const providerLabel = (k: string) => providers.value.find((p) => p.key === k)?.label || k

function onProviderChange(k: string) {
  const p = providers.value.find((x) => x.key === k)
  if (p) { keyForm.base_url = p.base_url; keyForm.model = p.model }
}

async function loadKey() {
  try {
    const [ps, me] = await Promise.all([
      ApiClient.get<any>('/api/ai-key/providers'),
      ApiClient.get<any>('/api/ai-key/me'),
    ])
    providers.value = ps?.data || []
    keyMeta.value = me?.data || null
    if (keyMeta.value) {
      keyForm.provider = keyMeta.value.provider
      keyForm.base_url = keyMeta.value.base_url
      keyForm.model = keyMeta.value.model
    } else {
      onProviderChange(keyForm.provider)
    }
  } catch { /* 未登录或接口不可用时不阻塞页面 */ }
}

async function testKey() {
  if (!keyForm.api_key.trim()) { ElMessage.warning('请先填入密钥'); return }
  keyTesting.value = true
  try {
    const res: any = await ApiClient.post('/api/ai-key/test', { ...keyForm })
    if (res?.success) ElMessage.success(res.message || '连接正常')
    else ElMessage.error(res?.message || '连接失败')
  } catch (e: any) {
    ElMessage.error(e?.message || '连接失败')
  } finally { keyTesting.value = false }
}

async function saveKey() {
  if (!keyForm.api_key.trim()) { ElMessage.warning('请先填入密钥'); return }
  keySaving.value = true
  try {
    const res: any = await ApiClient.post('/api/ai-key/save', { ...keyForm })
    if (res?.success) {
      keyMeta.value = res.data
      keyForm.api_key = ''
      ElMessage.success('已保存，AI 功能已开启')
      await loadPage()
    } else ElMessage.error(res?.message || '保存失败')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally { keySaving.value = false }
}

async function deleteKey() {
  keyDeleting.value = true
  try {
    await ApiClient.delete('/api/ai-key/me')
    keyMeta.value = null
    ElMessage.success('已删除')
    await loadPage()
  } catch (e: any) {
    ElMessage.error(e?.message || '删除失败')
  } finally { keyDeleting.value = false }
}

const pushForm = reactive({
  serverchan_key: '',
  pushplus_token: '',
  enabled: true,
})


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
    ElMessage.error(e?.message || '设置加载失败')
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

onMounted(() => { loadPage(); loadKey() })
</script>

<style scoped lang="scss">
.membership { max-width: 820px; margin: 0 auto; padding: 16px; }
.page-head { margin-bottom: 16px; }
.page-head h2 { margin: 0 0 4px; font-size: 22px; }
.page-head p { margin: 0; color: var(--el-text-color-secondary); }
.card { margin-bottom: 16px; border-radius: 8px; }
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
.card-hint { font-size: 12px; color: var(--el-text-color-secondary); }
.muted { color: var(--el-text-color-secondary); line-height: 1.7; }
.push-form { margin-top: 14px; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; }
.account-line { margin: 0 0 12px; color: var(--el-text-color-regular);
  em { font-style: normal; color: var(--el-text-color-placeholder); }
}
.disclaimer { color: #909399; font-size: 12px; text-align: center; line-height: 1.6; }

@media (max-width: 720px) {
  .bound-box { grid-template-columns: 1fr; }
}
</style>
