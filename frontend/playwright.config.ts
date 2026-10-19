import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests',use:{baseURL:'http://127.0.0.1:5173',channel:process.env.QUANTPILOT_BROWSER_CHANNEL||undefined,trace:'retain-on-failure'},
  webServer:{command:'npm run dev',url:'http://127.0.0.1:5173',reuseExistingServer:!process.env.CI},
  projects:[{name:'chromium',use:{browserName:'chromium'}}]});

