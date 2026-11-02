import {test,expect,type Page} from '@playwright/test';

const ready=(ok:boolean)=>({ready:ok,checked_at:new Date().toISOString(),checks:{database:{ok:true,status:'connected'},
  completed_candles:{ok,status:ok?'fresh':'stale',age_seconds:ok?120:4000},
  analysis_snapshot:{ok:true,status:'fresh',age_seconds:60,built_at:new Date(Date.now()-60000).toISOString()}},
  collector:{status:'connected',age_seconds:3,poll_interval_seconds:5}});

async function setup(page:Page,ok:boolean){
  await page.route('**/api/v1/**',route=>{
    const key=new URL(route.request().url()).pathname.split('/').at(-1)!;
    const values:Record<string,unknown>={ready:ready(ok),coverage:{release_ready:false},'strategy-research':{forward:{}},candles:[]};
    return route.fulfill({json:{data:values[key]??{},provenance:{source:'Browser operations test fixture',freshness:'fresh',observed_at:null},
      model_version:null,config_version:'fixture'}});
  });
  await page.goto('/');await page.getByRole('button',{name:'More',exact:true}).click();
  await page.getByRole('list',{name:'More pages'}).getByRole('button',{name:/^System/}).click();
}

test('system health lists each readiness check and the analysis snapshot',async({page})=>{
  await setup(page,true);
  const r=page.getByRole('region',{name:'Readiness'});
  await expect(r).toContainText('Ready');
  await expect(r).toContainText('completed candles');
  await expect(r).toContainText('analysis snapshot');
  await expect(page.getByRole('region',{name:'Data collection'})).toContainText('Analysis snapshot');
});

test('stale candles show the system needs attention',async({page})=>{
  await setup(page,false);
  const r=page.getByRole('region',{name:'Readiness'});
  await expect(r).toContainText('Needs attention');
  await expect(r).toContainText('stale');
});
