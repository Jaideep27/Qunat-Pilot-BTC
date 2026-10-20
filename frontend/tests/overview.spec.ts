import {test,expect,type Page} from '@playwright/test';

const forecast=(horizon:string,p:number,extra:object={})=>({horizon,cutoff:'2026-10-20T09:00:00+00:00',status:'no_validated_skill',
  probability_up:p,base_rate:p,return_q10:-.01,return_q90:.012,range_status:'model',drop5_risk:.05,regime:'ranging',
  volatility:{ewma:.004},...extra});
const values:Record<string,unknown>={
  candles:Array.from({length:12},(_,i)=>({event_time:new Date(Date.UTC(2026,9,20,8,i*5)).toISOString(),available_at:new Date(Date.UTC(2026,9,20,8,i*5+5)).toISOString(),
    open:80000+i*10,high:80050+i*10,low:79950+i*10,close:80010+i*10,volume:10})),
  indicators:{rsi_14:55,'1h_rsi_14':61,adx:22,relative_volume:1.3,funding_rate:.0001,open_interest:94000,global_ratio:1.5,top_position_ratio:1.8,fear_greed:58},
  forecasts:{latest:[forecast('1h',.505),forecast('4h',.51),forecast('24h',.519)],forward:{'4h':{scored:50,brier:.2498,constant_brier:.2498}}},
  macro:{upcoming:{fomc:{id:'fomc',kind:'fomc',label:'FOMC rate decision',scheduled_at:'2099-01-01T18:00:00+00:00'},cpi:null,jobs:null},recent:[]},
  news:{items:[{id:'n1',title:'Bitcoin climbs as ETF flows return',url:'https://example.com/n1',source:'coindesk',available_at:'2026-10-20T08:00:00+00:00'}]},
  'strategy-research':{status:'complete',trials:{swing:81,intraday:113},passing:[],holdout_evaluated:false,next_test:'2027-04-20',families:[],forward:{}},
  ready:{ready:true,checks:{database:{ok:true,status:'connected'}},collector:{status:'connected',age_seconds:3},worker:{status:'healthy',next_scheduled_at:'2099-01-01T00:00:00Z'}},
  health:{status:'ok'},threads:[],answers:{history:[],scoreboard:{}},
};
const answer={id:'a1',question:{type:'touch',horizon_hours:168},price:80000,stale:false,warnings:[],
  headline:{kind:'probability',probability:.21,base_rate:.18,method:'calibrated_volatility',confidence:'weak'},range:null,ai:{status:'off'}};

async function open(page:Page,overrides:Record<string,unknown>={}){
  const asked:string[]=[];
  await page.route('**/api/v1/**',route=>{
    const url=new URL(route.request().url()),key=url.pathname.split('/').at(-1)!;
    if(key==='ask'){asked.push(JSON.parse(route.request().postData()??'{}').question);return route.fulfill({json:{data:answer}})}
    const data=key in overrides?overrides[key]:values[key];
    return route.fulfill({json:{data:data??{},provenance:{source:'fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});
  });
  await page.goto('/');
  return asked;
}

test('the overview outlook switches horizons and labels a no-edge forecast honestly',async({page})=>{
  await open(page);
  const outlook=page.getByRole('region',{name:'Outlook'});
  await expect(outlook).toContainText('51.0%');
  await expect(outlook).toContainText('No edge');
  await outlook.getByRole('button',{name:'24h',exact:true}).click();
  await expect(outlook).toContainText('51.9%');
  await expect(outlook).toContainText('Chance of touching −5% in 24h');
  await expect(page.getByRole('region',{name:'Next macro event'})).toContainText('FOMC rate decision');
  await expect(page.getByRole('region',{name:'Latest headlines'})).toContainText('Bitcoin climbs as ETF flows return');
});

test('the top-bar ask box jumps to Ask with an instant quant answer',async({page})=>{
  const asked=await open(page);
  await page.keyboard.press('/');
  await expect(page.getByRole('textbox',{name:'Ask a question'})).toBeFocused();
  await page.keyboard.type('Chance BTC touches 90k this week?');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading',{name:'Ask QuantPilot',level:1})).toBeVisible({timeout:30000});
  await expect(page.getByRole('region',{name:'QuantPilot chat'})).toContainText('21.0%');
  expect(asked).toEqual(['Chance BTC touches 90k this week?']);
});

test('glossary terms explain themselves on hover',async({page})=>{
  await open(page);
  const term=page.getByRole('region',{name:'Outlook'}).getByRole('button',{name:'Base rate'});
  await term.hover();
  await expect(page.getByRole('tooltip').filter({hasText:'How often this happened historically'})).toBeVisible();
});

test('a malformed response shows a message instead of blanking the app',async({page})=>{
  await open(page,{forecasts:{latest:'not a list'}});
  await expect(page.getByRole('status').filter({hasText:'could not be displayed'})).toBeVisible();
  await page.getByRole('button',{name:'System',exact:true}).click();
  await expect(page.getByRole('heading',{name:'System health',level:1})).toBeVisible({timeout:30000});
});
