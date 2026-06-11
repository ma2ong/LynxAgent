<template>
  <div class="membership">
    <h2>会员与用量</h2>

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
        <li>每日 AI 深度分析 3 → 30 次</li>
        <li>解锁催化剂深度报告</li>
        <li>解锁因子实验室与策略回测</li>
      </ul>
      <el-alert type="info" :closable="false"
        title="当前为内测期，开通方式：添加微信并备注注册用户名，人工开通。"
      />
      <div class="contact">
        <!-- TODO(运营)：上线前替换为真实收款码图片与微信号 -->
        <p>联系微信：<b>（上线前填写）</b></p>
      </div>
    </el-card>

    <p class="disclaimer">
      本产品为 AI 研究工具，所有内容仅供研究参考，不构成投资建议。市场有风险，决策需独立。
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchBillingMe, type BillingMe } from '@/api/billing'

const info = ref<BillingMe | null>(null)
const usageLabel = computed(() => info.value ? `${info.value.used_today}/${info.value.daily_limit}` : '')

onMounted(async () => {
  try {
    const res = await fetchBillingMe()
    info.value = (res?.data as BillingMe) ?? null
  } catch (e: any) {
    ElMessage.error(e?.message || '用量信息加载失败')
  }
})
</script>

<style scoped lang="scss">
.membership { max-width: 720px; margin: 0 auto; padding: 16px; }
.card { margin-bottom: 16px; }
.plan-line { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.expires { color: #909399; font-size: 13px; }
.usage { display: flex; flex-direction: column; gap: 8px; font-size: 14px; color: #606266; }
.benefits { margin: 8px 0 16px; padding-left: 20px; color: #606266; line-height: 1.9; }
.contact { margin-top: 12px; font-size: 14px; }
.disclaimer { color: #c0c4cc; font-size: 12px; text-align: center; }
</style>
