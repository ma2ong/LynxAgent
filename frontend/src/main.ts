import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import ElementPlus from 'element-plus'
import * as ElementPlusIcons from '@element-plus/icons-vue'
import 'element-plus/dist/index.css'

const app = createApp(App)

// Register every Element Plus icon as a global component.
Object.entries(ElementPlusIcons).forEach(([name, icon]) => {
  app.component(name, icon as any)
})

app.use(router).use(ElementPlus).mount('#app')
