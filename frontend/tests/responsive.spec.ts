import {test,expect,type Page} from '@playwright/test';

// Representative data so every page renders its real layout (cards, tables, charts) at each size.
const candles=Array.from({length:60},(_,i)=>({event_time:new Date(Date.UTC(2026,9,20,8,i*5)).toISOString(),
  available_at:new Date(Date.UTC(2026,9,20,8,i*5+5)).toISOString(),open:80000+i*10,high:80050+i*10,low:79950+i*10,close:80010+i*10,volume:10}));
const forecast=(horizon:string,p:number)=>({horizon,cutoff:'2026-10-20T09:00:00+00:00',status:'no_validated_skill',probability_up:p,base_rate:p,
  return_q10:-.01,return_q90:.012,range_status:'model',drop5_risk:.05,regime:'ranging',volatility:{ewma:.004},champion:{group:'constant',training:null}});
const values:Record<string,unknown>={
  candles,
  forecasts:{latest:[forecast('1h',.505),forecast('4h',.51),forecast('24h',.519)],forward:{},champions:{},backtest_overfitting:{},status:'running'},
  macro:{upcoming:{fomc:{id:'fomc',kind:'fomc',label:'FOMC rate decision',scheduled_at:'2099-01-01T18:00:00+00:00'},cpi:null,jobs:null},recent:[]},
  news:{items:[{id:'n1',title:'Bitcoin climbs as ETF flows return',url:'https://example.com/n1',source:'coindesk',available_at:'2026-10-20T08:00:00+00:00',published_at:null,excerpt:'Flows returned.'}],runs:[],providers:{},feeds:[{source:'coindesk',status:'ok',checked_at:'2026-10-20T08:00:00+00:00'}],total_items:1,model:'fixture',scope:'Fixture.'},
  'strategy-research':{status:'complete',trials:{swing:81,intraday:113},passing:[],holdout_evaluated:false,next_test:'2027-04-20',families:[],forward:{}},
  ready:{ready:true,checks:{database:{ok:true,status:'connected'}},collector:{status:'connected',age_seconds:3,poll_interval_seconds:5}},
  health:{status:'ok'},threads:[],answers:{history:[],scoreboard:{}},
  archive:{accounts:[{id:'acct-master',status:'RETIRED',retired_at:'2026-11-02T10:00:00+00:00',equity:10000,trades:0}]},
  backtests:{status:'blocked',comparisons:[],benchmarks:{}},studies:{catalogue:[],series:{}},
  storage:{engine:'postgresql',markets:[],feeds:[]},
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
const pages=[['overview','Home'],['indicators','Indicators'],['strategies','Strategies'],['archive','Trading history'],['more','More'],['chart','Chart'],['ask','Ask'],['research','Research'],
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
  await expect(page.getByRole('heading',{name:'Home',level:1})).toBeVisible({timeout:30_000});
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
  await expect(page.getByRole('heading',{name:'Home',level:1})).toBeVisible({timeout:30_000});
  await page.keyboard.press('Control+k');
  const palette=page.getByRole('dialog',{name:'Command palette'});
  await expect(palette).toBeVisible();
  await page.keyboard.type('tradi');
  await expect(palette.getByRole('option').first()).toHaveText(/Go to Trading history/);
  await page.keyboard.press('Enter');
  await expect(palette).toBeHidden();
  await expect(page.getByRole('heading',{name:'Trading history',level:1})).toBeVisible();
  await page.keyboard.press('Alt+2');
  await expect(page.getByRole('heading',{name:'Indicators',level:1})).toBeVisible();
  await page.keyboard.press('Control+k');
  await expect(palette).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(palette).toBeHidden();
});

test('Escape closes the command palette even before its search box has focus',async({page})=>{
  await mock(page);
  await page.setViewportSize({width:1366,height:768});
  await page.goto('/#overview');
  await expect(page.getByRole('heading',{name:'Home',level:1})).toBeVisible({timeout:30_000});
  await page.keyboard.press('Control+k');
  const palette=page.getByRole('dialog',{name:'Command palette'});
  await expect(palette).toBeVisible();
  await page.evaluate(()=>(document.activeElement as HTMLElement|null)?.blur());
  await page.keyboard.press('Escape');
  await expect(palette).toBeHidden();
});

test('the sidebar collapses, remembers it and the active marker follows the page',async({page})=>{
  await mock(page);
  await page.setViewportSize({width:1440,height:900});
  await page.goto('/#overview');
  await expect(page.getByRole('heading',{name:'Home',level:1})).toBeVisible({timeout:30_000});
  const nav=page.getByRole('navigation',{name:'Main navigation'});
  const wide=(await nav.boundingBox())!.width;
  await page.getByRole('button',{name:'Collapse sidebar'}).click();
  await expect.poll(async()=>(await nav.boundingBox())!.width).toBeLessThan(wide-100);
  await page.reload();
  await expect(page.getByRole('button',{name:'Expand sidebar'})).toBeVisible({timeout:30_000});
  await page.getByRole('button',{name:'More',exact:true}).click();await page.getByRole('list',{name:'More pages'}).getByRole('button',{name:/^Trading history/}).click();
  const marker=page.locator('.nav-indicator');
  await expect.poll(async()=>{const m=(await marker.boundingBox())!,b=(await page.getByRole('button',{name:'More',exact:true}).boundingBox())!;
    return Math.abs(m.y-b.y)}).toBeLessThan(2);
  await page.getByRole('button',{name:'Expand sidebar'}).click();
  await expect.poll(async()=>(await nav.boundingBox())!.width).toBeGreaterThan(wide-5);
});
