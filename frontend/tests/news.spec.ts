import {test,expect,type Page} from '@playwright/test';

const item={id:'item-1',cluster:'c1',source:'bls',url:'https://www.bls.gov/news.release/cpi.nr0.htm',
  title:'Consumer Price Index rises 0.3 percent',excerpt:'CPI increased in September.',
  published_at:'2026-10-19T12:30:00Z',available_at:'2026-10-19T12:31:00Z'};

function news(status:string){return {items:[item],feeds:[{source:'bls',status:'ok',checked_at:'2026-10-19T12:31:00Z'}],
  runs:[{id:'run-1',status:'completed',available_at:'2026-10-19T12:40:00Z',result:{events:[{summary:'CPI rose 0.3 percent.',
    sentiment:'negative',novelty:'new',entities:['BLS','CPI'],uncertainties:['Consensus is unavailable.'],source_ids:['item-1']}]}}],
  analysis_status:status,total_items:1,scope:'Feed excerpts.',model:'gpt-6-luna',forecasting_status:'shadow_only_no_policy_changes'};}

const cpi={id:'cpi:2026-10-14',kind:'cpi',label:'Consumer Price Index',scheduled_at:'2026-10-14T12:30:00+00:00',status:'released',
  values:{CPIAUCSL:{period:'2026-09-01',value:336.3,previous:334.1,change:2.2,change_pct:0.47},
    CPILFESL:{period:'2026-09-01',value:330,previous:329.3,change:0.7,change_pct:0.21}},surprise:null,surprise_status:'unavailable_no_timestamped_consensus'};
const macro={upcoming:{cpi:{...cpi,id:'cpi:2026-11-12',scheduled_at:'2026-11-12T13:30:00+00:00',status:'scheduled',values:{}},
  jobs:null,fomc:{id:'fomc:2026-10-28',kind:'fomc',label:'FOMC rate decision',scheduled_at:'2026-10-28T18:00:00+00:00',status:'scheduled',values:{}}},
  recent:[cpi],total_events:180,surprise_status:'unavailable_no_timestamped_consensus',scope:'Official schedules.'};

async function open(page:Page,status:string,newsData?:unknown){
  const requested:string[]=[];
  const values:Record<string,unknown>={candles:[],indicators:{},positioning:[],sentiment:[],
    predictions:{action:'NO_TRADE',reasons:['MODEL_RELEASE_BLOCKED']},coverage:{release_ready:false},
    backtests:{status:'blocked',comparisons:[],benchmarks:{}},
    portfolio:{equity:10000,position:null,fills:[],decisions:[],paused:false}};
  await page.route('**/api/v1/**',route=>{
    const key=new URL(route.request().url()).pathname.split('/').at(-1)!;requested.push(key);
    return route.fulfill({json:{data:key==='news'?(newsData??news(status)):key==='macro'?macro:values[key],
      provenance:{source:'News test fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});
  });
  await page.goto('/');await page.waitForLoadState('networkidle');
  // Record only the requests made by the News tab itself.
  requested.length=0;
  await page.getByRole('button',{name:'News',exact:true}).click();
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
  await expect(page.getByRole('heading',{level:1})).toHaveText('News & macro');
  expect(requested).not.toContain('portfolio');
});

test('current analysis shows no delay notice',async({page})=>{
  await open(page,'current');
  const panel=page.getByRole('region',{name:'News evidence'});
  await expect(panel).toContainText('CPI rose 0.3 percent.');
  await expect(panel.getByText('Analysis delayed')).toHaveCount(0);
});

test('macro calendar shows upcoming releases and first-release values without inventing a surprise',async({page})=>{
  await open(page,'current');
  const calendar=page.getByRole('region',{name:'Macro calendar'});
  await expect(calendar).toContainText('FOMC rate decision');
  await expect(calendar).toContainText('Consumer Price Index');
  await expect(calendar).toContainText('Headline +0.47% m/m');
  await expect(calendar).toContainText('Core +0.21% m/m');
  await expect(calendar).toContainText('Consensus surprise unavailable');
  await expect(calendar.getByText('Employment Situation')).toHaveCount(0);
});

test('each reasoner reports its own status and analyses name the model that produced them',async({page})=>{
  const data=news('delayed_usage_limit');
  await open(page,'delayed_usage_limit',{...data,runs:[{...data.runs[0],provider:'claude',model:'claude-sonnet-5-5'}],
    providers:{claude:{model:'claude-sonnet-5-5',analysis_status:'current'},
      codex:{model:'gpt-6-luna',analysis_status:'delayed_usage_limit'}}});
  const panel=page.getByRole('region',{name:'News evidence'});
  await expect(panel.getByRole('list',{name:'News reasoners'})).toContainText('claude · claude-sonnet-5-5 · current');
  await expect(panel.getByRole('list',{name:'News reasoners'})).toContainText('codex · gpt-6-luna · delayed: usage limit');
  await expect(panel).toContainText('Analysed by claude-sonnet-5-5');
});
