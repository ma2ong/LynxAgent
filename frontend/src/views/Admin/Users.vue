<template>
  <div class="admin-users">
    <h2>用户管理</h2>
    <el-table :data="users" v-loading="loading" stripe>
      <el-table-column prop="username" label="用户名" width="140" />
      <el-table-column prop="email" label="邮箱" min-width="180" />
      <el-table-column label="套餐" width="160">
        <template #default="{ row }">
          <el-tag :type="row.plan === 'member' ? 'warning' : 'info'" size="small">
            {{ row.plan === 'member' ? '会员' : '免费' }}
          </el-tag>
          <div v-if="row.plan_expires_at" class="sub">至 {{ row.plan_expires_at }}</div>
        </template>
      </el-table-column>
      <el-table-column prop="used_today" label="今日用量" width="90" />
      <el-table-column prop="used_total" label="累计" width="80" />
      <el-table-column prop="last_login" label="最近登录" width="170">
        <template #default="{ row }">{{ (row.last_login || '').slice(0, 16) || '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" plain @click="openPlanDialog(row)">改套餐</el-button>
          <el-button size="small" :type="row.is_active ? 'danger' : 'success'" plain
                     :disabled="row.is_admin === 1" @click="toggleActive(row)">
            {{ row.is_active ? '停用' : '启用' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="修改套餐" width="360px">
      <el-form label-width="80px">
        <el-form-item label="套餐">
          <el-radio-group v-model="editPlan">
            <el-radio label="free">免费版</el-radio>
            <el-radio label="member">会员版</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="editPlan === 'member'" label="到期日">
          <el-date-picker v-model="editExpires" type="date" value-format="YYYY-MM-DD"
                          placeholder="留空 = 长期" clearable />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="savePlan">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { adminListUsers, adminSetPlan, adminSetActive, type AdminUser } from '@/api/billing'

const users = ref<AdminUser[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const editPlan = ref('free')
const editExpires = ref<string | null>(null)
let editingUser: AdminUser | null = null

async function load() {
  loading.value = true
  try {
    const res = await adminListUsers()
    users.value = (res?.data as AdminUser[]) ?? []
  } finally {
    loading.value = false
  }
}

function openPlanDialog(row: AdminUser) {
  editingUser = row
  editPlan.value = row.plan
  editExpires.value = row.plan_expires_at
  dialogVisible.value = true
}

async function savePlan() {
  if (!editingUser) return
  try {
    await adminSetPlan(editingUser.username, editPlan.value,
      editPlan.value === 'member' ? editExpires.value || null : null)
    ElMessage.success('已更新')
    dialogVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  }
}

async function toggleActive(row: AdminUser) {
  try {
    await adminSetActive(row.username, !row.is_active)
    ElMessage.success(row.is_active ? '已停用' : '已启用')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  }
}

onMounted(load)
</script>

<style scoped lang="scss">
.admin-users { padding: 16px; }
.sub { font-size: 11px; color: #909399; }
</style>
