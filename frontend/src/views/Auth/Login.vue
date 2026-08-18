<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2 class="title">AStockPick {{ mode === 'login' ? '登录' : '注册' }}</h2>

      <!-- Login form -->
      <el-form v-if="mode === 'login'" @submit.prevent="onSubmit">
        <el-form-item>
          <el-input v-model="username" placeholder="用户名" :prefix-icon="User" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="password" type="password" placeholder="密码" :prefix-icon="Lock" show-password />
        </el-form-item>
        <el-button type="primary" :loading="loading" class="submit" @click="onSubmit">登录</el-button>
      </el-form>

      <!-- Register form -->
      <el-form v-else @submit.prevent="doRegister">
        <el-form-item>
          <el-input v-model="regForm.username" placeholder="用户名" :prefix-icon="User" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="regForm.email" placeholder="邮箱或手机号" :prefix-icon="Message" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="regForm.password" type="password" placeholder="密码" :prefix-icon="Lock" show-password />
        </el-form-item>
        <el-form-item>
          <el-input v-model="regForm.confirm_password" type="password" placeholder="确认密码" :prefix-icon="Lock" show-password />
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="agreedDisclaimer">
            我已阅读并同意
            <router-link to="/legal/terms" target="_blank">《用户协议》</router-link>、
            <router-link to="/legal/privacy" target="_blank">《隐私政策》</router-link>，
            并理解本产品仅供研究参考，不构成投资建议
          </el-checkbox>
        </el-form-item>
        <el-button type="primary" :loading="loading" class="submit" @click="doRegister">注册</el-button>
      </el-form>

      <!-- Mode toggle -->
      <div class="toggle">
        <template v-if="mode === 'login'">
          没有账号？<el-button text @click="mode = 'register'">注册</el-button>
        </template>
        <template v-else>
          已有账号？<el-button text @click="mode = 'login'">登录</el-button>
        </template>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock, Message } from '@element-plus/icons-vue'
import { ApiClient } from '@/api/request'

const router = useRouter()
// 官网的「免费开始」直接落到注册表单，别让访客到了页面还要先找一下注册在哪。
const mode = ref<'login' | 'register'>(useRoute().query.register ? 'register' : 'login')
const username = ref('')
const password = ref('')
const loading = ref(false)

const regForm = ref({ username: '', email: '', password: '', confirm_password: '' })
const agreedDisclaimer = ref(false)

const onSubmit = async () => {
  if (!username.value || !password.value) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    const res = await ApiClient.post<any>('/api/auth/login', {
      username: username.value,
      password: password.value,
    })
    const token = res?.data?.access_token
    if (!token) throw new Error(res?.message || '登录失败')
    localStorage.setItem('auth-token', token)
    ElMessage.success('登录成功')
    // 登录后一律回盘面总览。之前是回 query 里的 redirect，而那个值是鉴权守卫在
    // 拦截时按「你当时停在哪一页」写进去的 —— 浏览器重开还在选股页，登录后又被送回选股页，
    // 于是首页形同虚设。深链接分享因此不再自动跳转，这是刻意取舍（Allen 2026-08-06 要求）。
    router.push('/dashboard')
  } catch (e: any) {
    ElMessage.error(e?.message || '登录失败')
  } finally {
    loading.value = false
  }
}

async function doRegister() {
  if (!agreedDisclaimer.value) {
    ElMessage.warning('请先阅读并勾选用户协议与免责声明')
    return
  }
  if (regForm.value.username.trim().length < 3) {
    ElMessage.warning('用户名至少 3 个字符')
    return
  }
  // 邮箱或中国大陆手机号，与后端 is_valid_contact 同一口径
  const contact = regForm.value.email.trim()
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(contact) && !/^1[3-9]\d{9}$/.test(contact)) {
    ElMessage.warning('请输入有效的邮箱或手机号')
    return
  }
  if (regForm.value.password.length < 6) {
    ElMessage.warning('密码至少 6 位')
    return
  }
  if (regForm.value.password !== regForm.value.confirm_password) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }
  loading.value = true
  try {
    await ApiClient.post('/api/auth/register', regForm.value)
    ElMessage.success('注册成功，请登录')
    mode.value = 'login'
  } catch (e: any) {
    ElMessage.error(e?.message || '注册失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7fa;
}
.login-card {
  width: 360px;
}
.title {
  text-align: center;
  margin: 0 0 20px;
}
.submit {
  width: 100%;
}
.toggle {
  margin-top: 12px;
  text-align: center;
  font-size: 13px;
  color: #606266;
}
</style>
