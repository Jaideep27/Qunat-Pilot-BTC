import {test,expect} from '@playwright/test';

test('overview avoids downloading research and portfolio until their views are opened',async({page})=>{
  const requested:string[]=[];
  const data:Record<string,unknown>={candles:[],indicators:{},positioning:[],sentiment:[],
    predictions:{action:'NO_TRADE',reasons:['MODEL_RELEASE_BLOCKED']},
    coverage:{release_ready:false,families:{}},
    backtests:{status:'blocked',comparisons:[],benchmarks:{}},
    portfolio:{equity:10000,position:null,paused:true,fills:[],decisions:[]}};
  await page.route('**/api/v1/**',route=>{
    const key=new URL(route.request().url()).pathname.split('/').at(-1)!;
    requested.push(key);
    return route.fulfill({json:{data:data[key],provenance:{source:'Browser loading fixture',
      observed_at:null,freshness:'missing',served_at:new Date().toISOString()},
      config_version:'fixture',model_version:null}});
  });
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'Overview',level:1})).toBeVisible();
  await expect.poll(()=>requested.includes('indicators')).toBe(true);
  expect(requested).not.toContain('backtests');
  expect(requested).not.toContain('portfolio');
  await page.getByRole('button',{name:'Research',exact:true}).click();
  await expect.poll(()=>requested.includes('backtests')).toBe(true);
  await page.getByText('Original release model (2026)').click();
  await expect(page.getByText('Research has not been released')).toBeVisible();
  expect(requested).not.toContain('portfolio');
  await page.getByRole('button',{name:'Paper',exact:true}).click();
  await expect.poll(()=>requested.includes('portfolio')).toBe(true);
  await expect(page.getByRole('heading',{name:'Execution ledger'})).toBeVisible();
});
