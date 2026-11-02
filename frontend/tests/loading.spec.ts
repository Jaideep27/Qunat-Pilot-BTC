import {test,expect} from '@playwright/test';

test('home avoids downloading research until the Research page is opened',async({page})=>{
  const requested:string[]=[];
  const data:Record<string,unknown>={candles:[],indicators:{},positioning:[],sentiment:[],
    coverage:{release_ready:false,families:{}},
    backtests:{status:'blocked',comparisons:[],benchmarks:{}},
    };
  await page.route('**/api/v1/**',route=>{
    const key=new URL(route.request().url()).pathname.split('/').at(-1)!;
    requested.push(key);
    return route.fulfill({json:{data:data[key],provenance:{source:'Browser loading fixture',
      observed_at:null,freshness:'missing',served_at:new Date().toISOString()},
      config_version:'fixture',model_version:null}});
  });
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'Home',level:1})).toBeVisible();
  await expect.poll(()=>requested.includes('candles')).toBe(true);
  expect(requested).not.toContain('backtests');
  await page.getByRole('button',{name:'More',exact:true}).click();await page.getByRole('list',{name:'More pages'}).getByRole('button',{name:/^Research/}).click();
  await expect.poll(()=>requested.includes('backtests')).toBe(true);
  await page.getByText('Original release model (2026)').click();
  await expect(page.getByText('Research has not been released')).toBeVisible();
});
