<template>
  <div class="legal">
    <h1>隐私政策</h1>
    <p class="updated">更新日期：2026-06-11</p>
    <h3>1. 收集的信息</h3>
    <p>注册时收集用户名与邮箱；使用过程中记录功能调用日志（用于配额计算与服务改进）。不收集身份证件、银行账户、证券账户等敏感信息。</p>
    <h3>2. 信息的使用</h3>
    <p>仅用于提供服务、配额管理与必要的服务通知，不向任何第三方出售或共享个人信息。</p>
    <h3>3. 数据存储与安全</h3>
    <p>密码以加盐哈希存储，明文不可还原。数据存储于服务运营方控制的服务器。</p>
    <h3>4. 注销</h3>
    <p>用户可联系管理员注销账号，注销后个人信息将被删除。</p>
    <router-link to="/login">返回登录</router-link>
  </div>
</template>

<style scoped>
.legal { max-width: 720px; margin: 40px auto; padding: 0 24px; line-height: 1.8; color: #303133; }
.updated { color: #909399; font-size: 13px; }
</style>
