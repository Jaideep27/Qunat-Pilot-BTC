import {test,expect,type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';

// A snapshot built by the real backend from a synthetic market (tests/fixtures/analysis-snapshot.json).
const snapshot=JSON.parse(readFileSync(new URL('./fixtures/analysis-snapshot.json',import.meta.url),'utf8'));
// The indicator catalogue exactly as the API serves it (tests/fixtures/analysis-catalogue.json, from catalogue.public()).
const catalogueFixture=()=>JSON.parse(readFileSync(new URL('./fixtures/analysis-catalogue.json',import.meta.url),'utf8'));
const brief={id:'brief:1',created_at:new Date(Date.now()-3600_000).toISOString(),model:'claude-haiku-5-5',status:'ok',reason:'scheduled',
  brief:{headline:'Bitcoin drifts higher on a quiet day',overall:'leaning up',summary:'Price is up modestly and most trend readings point up.',
    trend:'Uptrend on the hourly chart.',momentum:'RSI is strong but not stretched.',volatility:'Normal.',volume_flow:'Buyers slightly ahead.',
    positioning:'Funding is normal.',strategies:'Trend Rider is bullish; the rest wait.',news_calendar:'CPI is ahead.',
    levels_to_watch:['Support near the 4h low'],risks:['Signals are mixed on the daily chart']}};
const explanation={title:'RSI explained',explanation:'RSI is in its strong zone on the hourly chart.',what_to_watch:['A drop below 55'],
  model:'claude-sonnet-5-5',created_at:new Date().toISOString()};

async function mock(page:Page,posted:{path:string;body:unknown}[]=[]){
  await page.route('**/api/v1/**',route=>{
    const url=new URL(route.request().url()),path=url.pathname.replace('/api/v1/','');
    if(route.request().method()==='POST'){posted.push({path,body:route.request().postDataJSON()});
      return route.fulfill({json:{data:path==='analysis/explain'?explanation:brief}})}
    const values:Record<string,unknown>={'analysis/snapshot':snapshot,'analysis/catalogue':catalogueFixture(),'analysis/briefs':{briefs:[brief],every_hours:4},
      'analysis/explanations':explanation,candles:[],news:{items:[]},macro:{upcoming:{},recent:[]},archive:{accounts:[
        {id:'acct-master',status:'RETIRED',retired_at:'2026-11-02T10:00:00+00:00',equity:10000,trades:0}]}};
    return route.fulfill({json:{data:values[path]??{},provenance:{source:'fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});
  });
}

test('home leads with Claude’s brief and shows the market in plain words',async({page})=>{
  await mock(page);
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'Home',level:1})).toBeVisible({timeout:30000});
  const b=page.getByRole('region',{name:'Market brief'});
  await expect(b).toContainText('Bitcoin drifts higher on a quiet day');
  await expect(b).toContainText('AI analysis by Claude Haiku');
  await expect(b).toContainText('not financial advice');
  const m=page.getByRole('region',{name:'Market now'});
  await expect(m).toContainText('Trending up (intraday)');
  await expect(m.getByRole('img',{name:/^Trend: \d of \d lean up/})).toBeVisible();
  await expect(page.getByRole('region',{name:'Strategies now'})).toContainText('Trend Rider');
  await expect(page.getByRole('region',{name:'Latest headlines'})).toContainText('Bitcoin climbs as ETF flows return');
  await expect(page.getByRole('region',{name:'Latest headlines'})).toContainText('CPI release');
});

test('indicators show a value, a zone bar and a plain reading per timeframe',async({page})=>{
  const posted:{path:string;body:unknown}[]=[];
  await mock(page,posted);
  await page.goto('/#indicators');
  const rsi=page.getByRole('article',{name:'RSI 14'});
  await expect(rsi).toBeVisible({timeout:30000});
  const value=(tf:string)=>snapshot.timeframes[tf].items.find((i:{id:string})=>i.id==='rsi_14').value.toFixed(1);
  await expect(rsi).toContainText(value('1h'));
  await expect(rsi.getByRole('img',{name:/^RSI 14: /})).toBeVisible();
  await page.getByRole('group',{name:'Timeframe'}).getByRole('button',{name:'4h'}).click();
  await expect(rsi).toContainText(value('4h'));
  await rsi.getByText('What is this?').click();
  await expect(rsi).toContainText('Below 30 = oversold');
  await rsi.getByRole('button',{name:'Explain with Claude'}).click();
  await expect(rsi.getByRole('region',{name:'Claude explanation'})).toContainText('RSI is in its strong zone');
  expect(posted).toEqual([{path:'analysis/explain',body:{kind:'indicator',ref:'rsi_14',timeframe:'4h',studies:[]}}]);
  await rsi.getByRole('button',{name:'Show on chart'}).click();
  await expect(page).toHaveURL(/#chart$/);
  expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('qp:studies')??'[]'))).toContain('rsi_14');
});

test('strategies show a live checklist, a verdict and the history against holding BTC',async({page})=>{
  await mock(page);
  await page.goto('/#strategies');
  const t=page.getByRole('article',{name:'Strategy Trend Rider'});
  await expect(t).toBeVisible({timeout:30000});
  await expect(t.getByRole('list',{name:'Trend Rider checklist'}).getByRole('listitem')).toHaveCount(4);
  await t.getByText('History test').click();
  await expect(t).toContainText('Holding BTC');
  await expect(t).toContainText('Positive in testing, not proven');
  await page.getByRole('textbox',{name:'Search setups'}).fill('sweep');
  await expect(page.getByRole('article',{name:/^Setup /})).toHaveCount(1);
  await expect(page.getByRole('article',{name:'Setup Sweep and reclaim of support'})).toBeVisible();
});

test('the public view can read Claude’s work but never call Claude',async({page})=>{
  const posted:{path:string;body:unknown}[]=[];
  await mock(page,posted);
  await page.goto('/?public#indicators');
  const rsi=page.getByRole('article',{name:'RSI 14'});
  await expect(rsi).toBeVisible({timeout:30000});
  await rsi.getByRole('button',{name:'Show Claude’s last explanation'}).click();
  await expect(rsi.getByRole('region',{name:'Claude explanation'})).toContainText('RSI is in its strong zone');
  await page.goto('/?public#overview');
  await expect(page.getByRole('region',{name:'Market brief'})).toContainText('Bitcoin drifts higher');
  await expect(page.getByRole('button',{name:/New brief/})).toHaveCount(0);
  expect(posted).toEqual([]);
});

test('five tabs, More lists the extras, and the old paper accounts are listed',async({page})=>{
  await mock(page);
  await page.goto('/#more');
  const nav=page.getByRole('navigation',{name:'Main navigation'});
  for(const tab of ['Home','Indicators','Strategies','Chart','More'])await expect(nav.getByRole('button',{name:tab,exact:true})).toBeVisible();
  const more=page.getByRole('list',{name:'More pages'});
  for(const name of ['News','Ask','Research','Trading history','System'])await expect(more).toContainText(name);
  await expect(more).not.toContainText('Rule engine');
  await more.getByRole('button',{name:/^Trading history/}).click();
  await expect(page.getByRole('heading',{name:'Trading history',level:1})).toBeVisible();
  await expect(page.getByRole('region',{name:'Past paper accounts'})).toContainText('Master Bot');
});

test('the analysis pages fit a phone screen',async({page})=>{
  await mock(page);
  await page.setViewportSize({width:375,height:812});
  for(const hash of ['#overview','#indicators','#strategies']){
    await page.goto('/'+hash);
    await expect(page.getByRole('heading',{level:1})).toBeVisible({timeout:30000});
    await page.waitForTimeout(200);
    const width=await page.evaluate(()=>({doc:document.documentElement.clientWidth,scroll:document.scrollingElement!.scrollWidth}));
    expect(width.scroll,hash).toBeLessThanOrEqual(width.doc+1);
  }
});
