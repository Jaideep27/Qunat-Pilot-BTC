import {test,expect} from '@playwright/test';

test('market to prediction to research to paper audit, with honest unreleased states',async({page})=>{
  await page.goto('/?preview=1');
  await expect(page.getByText('INTERFACE PREVIEW',{exact:false})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Overview',level:1})).toBeVisible();
  await expect(page.getByRole('img',{name:'BTCUSDT five-minute candlestick and volume chart'})).toBeVisible();
  await page.getByRole('button',{name:'Paper',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Decision gates'})).toBeVisible();
  await expect(page.getByText('MODEL RELEASE BLOCKED',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Research',exact:true}).click();
  await page.getByText('Original release model (2026)').click();
  await expect(page.getByText('Research has not been released')).toBeVisible();
  await page.getByRole('button',{name:'System',exact:true}).click();
  await expect(page.getByText('Historical coverage',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Paper',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Execution ledger'})).toBeVisible();
  await expect(page.getByText('No fills recorded')).toBeVisible();
  await expect(page.getByText('$10,000.00')).toBeVisible();
  await page.screenshot({path:'test-results/paper-audit.png',fullPage:true});
});

test('mobile navigation remains usable',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await page.goto('/?preview=1');
  await page.getByRole('button',{name:'Research',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Research',level:1})).toBeVisible();
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
  expect(overflow).toBe(false);
});

test('backend failure is explicit and retryable',async({page})=>{
  await page.route('**/api/v1/**',route=>route.abort());
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('services unavailable');
  await expect(page.getByRole('button',{name:'Retry',exact:true})).toBeVisible();
});



test('coverage distinguishes missing source timestamps from invalid values',async({page})=>{
  const data:Record<string,unknown>={candles:[],indicators:{},positioning:[],sentiment:[],
    predictions:{action:'NO_TRADE',reasons:['MODEL_RELEASE_BLOCKED']},
    coverage:{release_ready:true,strict_release_ready:false,quarantine:{source_rows:62,source_fraction:.00028,feature_lookback_bars:12,status:'accepted'},families:{positioning:{rows:221755,missing_windows:57,
      native_missing_count:5,invalid_rows:57,issues:['invalid numeric values','5 missing native observations']}}},
    backtests:{status:'blocked',comparisons:[],benchmarks:{}},
    portfolio:{equity:10000,position:null,paused:true,fills:[],decisions:[]}};
  await page.route('**/api/v1/**',route=>{
    const key=new URL(route.request().url()).pathname.split('/').at(-1)!;
    return route.fulfill({json:{data:data[key],provenance:{source:'Browser test fixture',observed_at:null,
      freshness:'missing',served_at:new Date().toISOString()},config_version:'test',model_version:null}});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'System',exact:true}).click();
  await expect(page.getByRole('columnheader',{name:'Missing source rows'})).toBeVisible();
  await expect(page.getByRole('columnheader',{name:'Invalid rows',exact:true})).toBeVisible();
  const row=page.getByRole('row').filter({has:page.getByRole('cell',{name:'positioning',exact:true})});
  await expect(row.getByRole('cell',{name:'5',exact:true})).toBeVisible();
  await expect(row.getByRole('cell',{name:'57',exact:true})).toHaveCount(2);
  await expect(row).toContainText('invalid numeric values');
  await expect(page.getByText('ELIGIBLE WITH QUARANTINE',{exact:true})).toBeVisible();
  await expect(page.getByText('62 affected source rows',{exact:false})).toBeVisible();
});
