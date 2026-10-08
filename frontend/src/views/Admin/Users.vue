<template>
  <div class="admin-users">
    <el-table :data="users" v-loading="loading" stripe>
      <el-table-column prop="username" label="用户名" width="140" />
      <el-table-column prop="email" label="邮箱" min-width="180" />
      <el-table-column prop="used_today" label="今日用量" width="90" />
      <el-table-column prop="used_total" label="累计" width="80" />
      <el-table-column prop="last_login" label="最近登录" width="170">
        <template #default="{ row }">{{ fmtLogin(row.last_login) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="110" fixed="right">
        <template #default="{ row }">
          <el-button size="small" :type="row.is_active ? 'danger' : 'success'" plain
                     :disabled="row.is_admin === 1" @click="toggleActive(row)">
            {{ row.is_active ? '停用' : '启用' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { adminListUsers, adminSetActive, type AdminUser } from '@/api/billing'

const users = ref<AdminUser[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const res = await adminListUsers()
    users.value = (res?.data as AdminUser[]) ?? []
  } finally {
    loading.value = false
  }
}

// 库里存的是 UTC ISO（带 T），直接截取会比北京时间慢 8 小时
function fmtLogin(value: string | null) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value.slice(0, 16)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
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

