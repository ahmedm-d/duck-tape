import {defineConfig,devices} from '@playwright/test';
export default defineConfig({
  testDir:'./tests',timeout:30000,fullyParallel:true,
  use:{baseURL:process.env.STEWARD_TEST_URL||'http://127.0.0.1:4175',trace:'retain-on-failure',screenshot:'only-on-failure'},
  projects:[{name:'chromium',use:{...devices['Desktop Chrome'],launchOptions:{executablePath:process.env.STEWARD_BROWSER_EXECUTABLE||undefined}}}],
  webServer:process.env.STEWARD_TEST_URL?undefined:{command:'npm run dev -- --host 127.0.0.1 --port 4175 --strictPort',url:'http://127.0.0.1:4175',reuseExistingServer:true},
});
