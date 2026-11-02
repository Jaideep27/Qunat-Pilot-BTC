import {defineConfig} from '@playwright/test';
// QUANTPILOT_UI_PORT lets a second checkout run its own dev server beside the live one on 5173.
const port=Number(process.env.QUANTPILOT_UI_PORT||5173);
export default defineConfig({testDir:'./tests',use:{baseURL:`http://127.0.0.1:${port}`,channel:process.env.QUANTPILOT_BROWSER_CHANNEL||undefined,trace:'retain-on-failure'},
  webServer:{command:`npm run dev -- --port ${port} --strictPort`,url:`http://127.0.0.1:${port}`,reuseExistingServer:!process.env.CI},
  projects:[{name:'chromium',use:{browserName:'chromium'}}]});

