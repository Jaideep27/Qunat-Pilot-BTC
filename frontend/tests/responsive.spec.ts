import {test,expect,type Page} from '@playwright/test';

// Representative data so every page renders its real layout (cards, tables, charts) at each size.
const candles=Array.from({length:60},(_,i)=>({event_time:new Date(Date.UTC(2026,9,20,8,i*5)).toISOString(),
  available_at:new Date(Date.UTC(2026,9,20,8,i*5+5)).toISOString(),open:80000+i*10,high:80050+i*10,low:79950+i*10,close:80010+i*10,volume:10}));
const forecast=(horizon:string,p:number)=>({horizon,cutoff:'2026-10-20T09:00:00+00:00',status:'no_validated_skill',probability_up:p,base_rate:p,
  return_q10:-.01,return_q90:.012,range_status:'model',drop5_risk:.05,regime:'ranging',volatility:{ewma:.004},champion:{group:'constant',training:null}});
const playbook=(id:string)=>({id,name:'Flush rebound with a deliberately long name to test wrapping',family:'dump',side:'long',source:['R11'],
  thesis:'A flush into support.',priority:1,shadow_only:false,trigger:{any:['DUMP'],within_bars:6,min_bars:1},regimes:['RANGE'],
  conditions:['trigger.oi_change <= -0.005'],abort:[],stop:{anchor:'trigger.low',buffer_atr:.25},target:{candidates:['vwap'],cap:'band4_high',fallback_r:2},
  time_stop_hours:12,breakeven_at_r:1,may_flip:false});
const values:Record<string,unknown>={
  candles,indicators:{rsi_14:55,'1h_rsi_14':61,adx:22,relative_volume:1.3,funding_rate:.0001,open_interest:94000,global_ratio:1.5,top_position_ratio:1.8,fear_greed:58},
  forecasts:{latest:[forecast('1h',.505),forecast('4h',.51),forecast('24h',.519)],forward:{},champions:{},backtest_overfitting:{},status:'running'},
  macro:{upcoming:{fomc:{id:'fomc',kind:'fomc',label:'FOMC rate decision',scheduled_at:'2099-01-01T18:00:00+00:00'},cpi:null,jobs:null},recent:[]},
  news:{items:[{id:'n1',title:'Bitcoin climbs as ETF flows return',url:'https://example.com/n1',source:'coindesk',available_at:'2026-10-20T08:00:00+00:00',published_at:null,excerpt:'Flows returned.'}],runs:[],providers:{},feeds:[{source:'coindesk',status:'ok',checked_at:'2026-10-20T08:00:00+00:00'}],total_items:1,model:'fixture',scope:'Fixture.'},
  'strategy-research':{status:'complete',trials:{swing:81,intraday:113},passing:[],holdout_evaluated:false,next_test:'2027-04-20',families:[],forward:{}},
  ready:{ready:true,checks:{database:{ok:true,status:'connected'}},collector:{status:'connected',age_seconds:3},worker:{status:'healthy'}},
  health:{status:'ok'},threads:[],answers:{history:[],scoreboard:{}},
  portfolio:{cash:10000,equity:10000,position:null,fees:0,realized_pnl:0,funding_total:0,fills:[],decisions:[],paused:false,halted:false},
  predictions:{action:'NO_TRADE',reasons:['WEAK_FORECAST'],probability:.51},
  rules:{rulebook:{hash:'db3d684ff71c',version:2,name:'QuantPilot'},layers:{ml:'shadow',news:'shadow',learner:'shadow',manage_rl:'shadow'},modes:{},
    playbooks:[playbook('PB01'),playbook('PB02')],account:null,checklist:[],research:null,provenance_note:'paper'},
  journal:{hits:[],trades:[],triggers:[]},research:{status:'not_run'},levels:{overlay:null,markers:[]},
  accounts:{config_hash:'x',model_hash:'y',overlay_time:null,note:'paper',watch_now:[{key:'global_ratio',label:'Long/short ratio (all traders)',value:1.99}],
    accounts:['Careful','Steady','Bold','Aggressive'].map((name,i)=>({id:'acct-'+'abcd'[i],name,status:i===1?'PAUSED_FOR_REVIEW':'RUNNING',equity:10000-i*123.45,
      balance:10000,account_return:-i*.0123,round:1,in_round:i*9,round_length:30,number:i*9,leverage:i+2,risk:.01+i*.005,percentile:90-i*10,cutoff_score:.2,
      full_risk_score:.3,daily_brake:.03,halted_day:false,floor_hit:false,position:i===0?{side:'long',quantity:.123,entry:80123.45,stop:79200,target:81600,
      liquidation:40200,leverage:2,entry_time:'2026-10-22T09:01:00+00:00',playbook:'PB08L',score:.4}:null,pending:false,
      last:{time:'2026-10-22T09:00:00+00:00',hit_id:'PB08L:x',playbook:'PB08L',side:'long',decision:'BELOW_CUTOFF',score:.1,cutoff:.2,confidence:.5,passed:5,total:6},
      expectations:{trades_per_week:1.4,win_rate:.49,mean_r:-.11},review:i===1?{id:'acct-b:round-1',round:1,status:'ready'}:null,
      rules:['Takes a confirmed rulebook setup only when its score is at least +0.21 R: the top 10% of setups over 2021–2025.','Leverage 2× at most.'],
      watches:['Order-book imbalance and spread (recorded, not scored).']}))},
  'account-trades':{trades:[]},decisions:{decisions:[]},reviews:{reviews:[]},
  backtests:{status:'blocked',comparisons:[],benchmarks:{}},studies:{catalogue:[],series:{}},
  storage:{engine:'postgresql',markets:[],feeds:[]},operations:{runs:[],ready:true},
};

async function mock(page:Page){
  await page.route('**/api/v1/**',route=>{
    const key=new URL(route.request().url()).pathname.split('/').at(-1)!;
    return route.fulfill({json:{data:values[key]??{},provenance:{source:'fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});
  });
}

/** Nothing may stick out sideways: no page-level horizontal scroll and every card inside the viewport. */
async function noOverflow(page:Page,where:string){
  const report=await page.evaluate(()=>{
    const width=document.documentElement.clientWidth;
    const scroll=document.scrollingElement!.scrollWidth;
    const outside=[...document.querySelectorAll<HTMLElement>('.card,.panel,.strip,.page-head,.topbar,.sidebar,.collapsible')]
      .filter(el=>{const r=el.getBoundingClientRect();return r.width>0&&(r.left<-1||r.right>width+1)})
      .map(el=>el.className+' '+Math.round(el.getBoundingClientRect().right));
    return {width,scroll,outside};
  });
  expect(report.scroll,`${where}: page scrolls sideways`).toBeLessThanOrEqual(report.width+1);
  expect(report.outside,`${where}: elements outside the viewport`).toEqual([]);
}

const sizes=[[375,812,'phone'],[768,1024,'tablet portrait'],[1024,768,'tablet landscape'],[1366,768,'laptop'],
  [1280,600,'short window'],[1920,1080,'full HD'],[2560,1080,'ultra-wide']] as const;
const pages=[['overview','Overview'],['chart','Chart'],['ask','Ask'],['research','Research'],['rules','Rule engine'],['paper','Paper trading'],
  ['news','News'],['system','System health']] as const;

for(const [width,height,name] of sizes){
  test(`every page fits a ${name} window (${width}×${height})`,async({page})=>{
    test.setTimeout(90_000);
    await mock(page);
    const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
    await page.setViewportSize({width,height});
    for(const [hash,heading] of pages){
      await page.goto('/#'+hash);
      await expect(page.getByRole('heading',{level:1}).first(),errors.join(' | ')).toContainText(heading,{timeout:30_000});
      await page.waitForTimeout(150);
      await noOverflow(page,`${name} #${hash}`);
      const nav=page.getByRole('navigation',{name:'Main navigation'});
      await expect(nav).toBeVisible();
      const box=(await nav.boundingBox())!;
      expect(box.x>=0&&box.x+box.width<=width+1&&box.y+box.height<=height+1,`${name}: navigation inside the window`).toBeTruthy();
    }
  });
}

test('maximizing, restoring and shrinking the window keeps the layout and the chart in shape',async({page})=>{
  test.setTimeout(90_000);
  await mock(page);
  await page.setViewportSize({width:1920,height:1080});
  await page.goto('/#overview');
  await expect(page.getByRole('heading',{name:'Overview',level:1})).toBeVisible({timeout:30_000});
  const chart=page.getByRole('img',{name:'BTCUSDT five-minute candlestick and volume chart'});
  await expect(chart).toBeVisible();
  for(const [width,height] of [[800,600],[1366,768],[420,860],[2560,1440],[1100,700]] as const){
    await page.setViewportSize({width,height});
    await page.waitForTimeout(400);
    await noOverflow(page,`resized to ${width}×${height}`);
    const sizes=await chart.evaluate(el=>({box:el.getBoundingClientRect().width,canvas:(el.querySelector('table')??el).getBoundingClientRect().width}));
    expect(Math.abs(sizes.canvas-sizes.box),`chart follows its box at ${width}×${height}`).toBeLessThanOrEqual(2);
  }
});

test('the command palette and keyboard shortcuts switch pages',async({page})=>{
  await mock(page);
  await page.setViewportSize({width:1366,height:768});
  await page.goto('/#overview');
  await expect(page.getByRole('heading',{name:'Overview',level:1})).toBeVisible({timeout:30_000});
  await page.keyboard.press('Control+k');
  const palette=page.getByRole('dialog',{name:'Command palette'});
  await expect(palette).toBeVisible();
  await page.keyboard.type('rul');
  await expect(palette.getByRole('option')).toHaveCount(2);           // Go to Rules + Ask
  await page.keyboard.press('Enter');
  await expect(palette).toBeHidden();
  await expect(page.getByRole('heading',{name:'Rule engine',level:1})).toBeVisible();
  await page.keyboard.press('Alt+6');
  await expect(page.getByRole('heading',{name:'Paper trading',level:1})).toBeVisible();
  await page.getByRole('button',{name:'Open command palette'}).click();
  await page.keyboard.press('Escape');
  await expect(palette).toBeHidden();
});

test('the sidebar collapses, remembers it and the active marker follows the page',async({page})=>{
  await mock(page);
  await page.setViewportSize({width:1440,height:900});
  await page.goto('/#overview');
  await expect(page.getByRole('heading',{name:'Overview',level:1})).toBeVisible({timeout:30_000});
  const nav=page.getByRole('navigation',{name:'Main navigation'});
  const wide=(await nav.boundingBox())!.width;
  await page.getByRole('button',{name:'Collapse sidebar'}).click();
  await expect.poll(async()=>(await nav.boundingBox())!.width).toBeLessThan(wide-100);
  await page.reload();
  await expect(page.getByRole('button',{name:'Expand sidebar'})).toBeVisible({timeout:30_000});
  await page.getByRole('button',{name:'Rules',exact:true}).click();
  const marker=page.locator('.nav-indicator');
  await expect.poll(async()=>{const m=(await marker.boundingBox())!,b=(await page.getByRole('button',{name:'Rules',exact:true}).boundingBox())!;
    return Math.abs(m.y-b.y)}).toBeLessThan(2);
  await page.getByRole('button',{name:'Expand sidebar'}).click();
  await expect.poll(async()=>(await nav.boundingBox())!.width).toBeGreaterThan(wide-5);
});
