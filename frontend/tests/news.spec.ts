import {test,expect,type Page} from '@playwright/test';

const item={id:'item-1',cluster:'c1',source:'bls',url:'https://www.bls.gov/news.release/cpi.nr0.htm',
  title:'Consumer Price Index rises 0.3 percent',excerpt:'CPI increased in September.',
  published_at:'2026-10-19T12:30:00Z',available_at:'2026-10-19T12:31:00Z'};

function news(status:string){return {items:[item],feeds:[{source:'bls',status:'ok',checked_at:'2026-10-19T12:31:00Z'}],
  runs:[{id:'run-1',status:'completed',available_at:'2026-10-19T12:40:00Z',result:{events:[{summary:'CPI rose 0.3 percent.',
    sentiment:'negative',novelty:'new',entities:['BLS','CPI'],uncertainties:['Consensus is unavailable.'],source_ids:['item-1']}]}}],
  analysis_status:status,total_items:1,scope:'Feed excerpts.',model:'gpt-6-luna',forecasting_status:'shadow_only_no_policy_changes'};}

async function open(page:Page,status:string){
  const requested:string[]=[];
  const values:Record<string,unknown>={candles:[],indicators:{},positioning:[],sentiment:[],
    predictions:{action:'NO_TRADE',reasons:['MODEL_RELEASE_BLOCKED']},coverage:{release_ready:false},
    backtests:{status:'blocked',comparisons:[],benchmarks:{}},
    portfolio:{equity:10000,position:null,fills:[],decisions:[],paused:false}};
  await page.route('**/api/v1/**',route=>{
    const key=new URL(route.request().url()).pathname.split('/').at(-1)!;requested.push(key);
    return route.fulfill({json:{data:key==='news'?news(status):values[key],
      provenance:{source:'News test fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});
  });
  await page.goto('/');await page.waitForLoadState('networkidle');
  // Record only the requests made by the News tab itself.
  requested.length=0;
  await page.getByRole('button',{name:'News & events',exact:true}).click();
  await expect(page.getByRole('region',{name:'News evidence'})).toBeVisible();
  return requested;
}

test('usage-limited analysis is shown as delayed with the last completed evidence still visible',async({page})=>{
  const requested=await open(page,'delayed_usage_limit');
  const panel=page.getByRole('region',{name:'News evidence'});
  await expect(panel.getByRole('status')).toContainText('Analysis delayed');
  await expect(panel).toContainText('CPI rose 0.3 percent.');
  await expect(panel).toContainText('new information');
  await expect(panel).toContainText('BLS · CPI');
  await expect(page.getByRole('heading',{level:1})).toHaveText('What moved the market, and when.');
  expect(requested).not.toContain('portfolio');
});

test('current analysis shows no delay notice',async({page})=>{
  await open(page,'current');
  const panel=page.getByRole('region',{name:'News evidence'});
  await expect(panel).toContainText('CPI rose 0.3 percent.');
  await expect(panel.getByText('Analysis delayed')).toHaveCount(0);
});
