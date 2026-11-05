import './assets/main.css'
import './assets/liquid-glass.css'

import { createApp } from 'vue'
import App from './App.vue'
import { installGlobalErrorHandlers } from './utils/errorSink'

const app = createApp(App)
installGlobalErrorHandlers(app)
app.mount('#app')
