import {test,expect,type Page} from '@playwright/test';

const rules=(lev:number,risk:string)=>[`Takes a confirmed rulebook setup only when its score is at least +0.21 R: the top 10% of setups over 2021–2025.`,
  `Risks ${risk} of equity per trade.`,`Leverage ${lev}× at most; the stop must sit inside the liquidation price.`,'Pauses after 30 trades for a review with the owner.'];
const acct=(id:string,name:string,lev:number,extra:object={})=>({id,name,status:'RUNNING',equity:10000,balance:10000,account_return:0,round:1,in_round:0,
  round_length:30,number:0,leverage:lev,risk:.01,percentile:90,cutoff_score:.21,full_risk_score:.33,daily_brake:.03,halted_day:false,floor_hit:false,
  position:null,pending:false,last:null,expectations:{trades_per_week:1.4,win_rate:.49,mean_r:-.11},review:null,
  report:{trades:12,wins:5,losses:7,win_rate:.4167,total_net:123.4,account_return:.01234,expectancy:10.3,avg_win:80,avg_loss:-40,
    profit_factor:1.43,payoff:2,max_drawdown:-.031,longest_drawdown_hours:52,longest_losing_streak:3,hold_return:.045,vs_hold:-.0327,
    hold:{start_price:80000,end_price:83600}},
  rules:rules(lev,'1%'),watches:['Long/short ratios (all traders and top traders), funding rate and open interest.'],...extra});
const accounts={config_hash:'abc123def456',model_hash:'7996f4bd6a09',overlay_time:'2026-10-22T10:00:00+00:00',note:'Paper money only.',
  watch_now:[{key:'global_ratio',label:'Long/short ratio (all traders)',value:1.99},{key:'imbalance_15bps',label:'Order-book imbalance',value:.13}],
  accounts:[
    acct('acct-a','Careful',2,{in_round:12,number:12,equity:10123.4,account_return:.01234,
      position:{side:'long',quantity:.1,entry:80000,stop:79200,target:81600,liquidation:40200,leverage:2,entry_time:'2026-10-22T09:01:00+00:00',playbook:'PB08L',score:.4},
      last:{time:'2026-10-22T09:00:00+00:00',hit_id:'PB08L:x',playbook:'PB08L',side:'long',decision:'TAKEN',score:.4,cutoff:.21,confidence:.62,passed:6,total:6}}),
    acct('acct-b','Steady',3,{status:'PAUSED_FOR_REVIEW',in_round:30,number:30,equity:9650,account_return:-.035,review:{id:'acct-b:round-1',round:1,status:'ready'}}),
    acct('acct-c','Bold',4),acct('acct-d','Aggressive',5)]};
const trades={trades:[{id:'acct-a:PB01:t',account:'acct-a',round:1,number:12,playbook:'PB01',side:'long',entry_time:'2026-10-22T05:00:00+00:00',
  exit_time:'2026-10-22T06:00:00+00:00',net:-41.2,r:null,reason:'STOP',diagnosis:'FAIL_STOP_HUNT',payload:{entry:80000,exit:79200},
  explanation:{why:'Price swept the stop below support before turning up.',lesson:'Give flush rebounds room below the wick.'}}]};
const decisions={decisions:[{id:'acct-a:PB01:t',account:'acct-a',hit_id:'PB01:t',time:'2026-10-22T04:45:00+00:00',decision:'TAKEN',score:.3,confidence:.58,cutoff:.21,
  trade_id:'acct-a:PB01:t',payload:{ticks:[{condition:'up_15m == 1',ok:true},{condition:'taker_share_15m >= 0.5',ok:true}],
    watch:[{name:'book_imbalance',value:-.2,ok:false,note:'buy minus sell orders'}],sizing:{risk_amount:100,leverage:2}}}]};
const reviews={reviews:[{id:'acct-b:round-1',account:'acct-b',round:1,status:'ready',created_at:'2026-10-22T08:00:00+00:00',resumed_at:null,payload:{
  numbers:{trades:30,wins:11,win_rate:.367,total_net:-350,mean_r:-.12,max_drawdown:-.06},
  separators:[{name:'watch:book_imbalance',effect:.9,label:'consistent',winners:.8,losers:.2,n_winners:11,n_losers:19}],
  learner:{available:true,would_skip:30},manager:{available:true,trades:30,actual_mean_r:-.12,policy_mean_r:-.1,fixed_rule_mean_r:-.11,hold_mean_r:-.14},
  summary:{where_it_went_wrong:'Most losses were longs into falling order books.',why:'Sellers dominated.',suggestions:['Require buy-side book imbalance.']}}}]};

const health={overall:'WATCH',reasons:['1 setup(s) failed the data check in the last 24 hours and were held back.'],reference:'ok',checked_at:'2026-10-23T12:00:00+00:00',
  card:{model_hash:'7996f4bd6a09',matches:true,kind:'L2 logistic regression (win chance of a confirmed setup)',inputs:67,training_setups:7071,trained_from:'2021-10-20',
    trained_until:'2025-10-18',walk_forward:[{n:872,predicted:.22,actual:.33,mean_r:-.33}],dev_brier:.2496,held_back_in_training:.021,live_since:'2026-10-22T22:15:00+00:00',
    scored_setups:11,holdout:'2025-10-20 → 2026-10-19, untouched'},
  calibration:{n:42,wins:15,predicted:.41,actual:.357,lo:.23,hi:.51,brier:.24,dev_brier:.2496,verdict:'IN_LINE',bins:[{range:[0,.35],n:20,predicted:.3,actual:.25}]},
  drift:{verdict:'TOO_FEW',n:12,window:50,top:[]},
  data_checks:{checked_7d:12,failed_7d:1,failed_24h:1,skipped_7d:0,latest:{time:'2026-10-23T11:00:00+00:00',setup:'PB08S',reasons:['missing:f_premium']}},
  challenger:null};

async function mock(page:Page,resumed:string[]){
  await page.route('**/api/v1/**',route=>{
    const url=new URL(route.request().url());
    if(route.request().method()==='POST'){resumed.push(url.pathname);return route.fulfill({json:{data:{account:'acct-b',round:2}}});}
    const key=url.pathname.split('/').at(-1)!;
    const values:Record<string,unknown>={accounts,'account-trades':trades,decisions,reviews,'model-health':health,
      rules:{rulebook:{hash:'db3d684ff71c',version:2,name:'x'},layers:{},modes:{},playbooks:[],account:null,checklist:[],research:null,provenance_note:'paper',accounts:[]},
      journal:{hits:[],trades:[],triggers:[]},research:{status:'not_run'},candles:[],indicators:{}};
    return route.fulfill({json:{data:values[key]??{},provenance:{source:'fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});
  });
}

test('four account cards show rules, progress, the open position and the last decision',async({page})=>{
  await mock(page,[]);
  await page.goto('/#rules');
  await expect(page.getByRole('heading',{name:'Rule engine',level:1})).toBeVisible({timeout:30000});
  const a=page.getByRole('region',{name:'Account Careful'});
  await expect(a).toContainText('Trade 12 / 30');
  await expect(a).toContainText('Leverage 2×');
  await expect(a).toContainText('$80,000.00');
  await expect(a).toContainText('6/6');
  await expect(a).toContainText('1.99');
  await expect(a.getByRole('button',{name:/Resume/})).toHaveCount(0);
  const card=a.getByRole('group',{name:'Careful report card'});
  await expect(card).toContainText('Holding BTC instead');
  await expect(card).toContainText('+4.50%');
  await expect(card).toContainText('1.43');
  await expect(card).toContainText('3 in a row');
  for(const name of ['Steady','Bold','Aggressive'])await expect(page.getByRole('region',{name:'Account '+name})).toBeVisible();
});

test('a paused account shows its review and resumes on request',async({page})=>{
  const resumed:string[]=[];
  await mock(page,resumed);
  await page.goto('/#rules');
  const b=page.getByRole('region',{name:'Account Steady'});
  await expect(b).toContainText('Paused for review');
  await b.getByRole('button',{name:'Open review'}).click();
  const review=page.getByRole('region',{name:'Round review'});
  await expect(review).toContainText('Most losses were longs into falling order books.');
  await expect(review).toContainText('Require buy-side book imbalance.');
  await expect(review).toContainText('consistent');
  await b.getByRole('button',{name:'Resume Steady'}).click();
  await expect.poll(()=>resumed).toEqual(['/api/v1/rules/accounts/acct-b/resume']);
});

test('the public view is read-only: no resume, no Ask, no chat, and it says so',async({page})=>{
  const resumed:string[]=[];
  await mock(page,resumed);
  await page.goto('/?public#rules');
  const b=page.getByRole('region',{name:'Account Steady'});
  await expect(b).toContainText('Paused for review',{timeout:30000});
  await expect(b.getByRole('button',{name:'Open review'})).toBeVisible();
  await expect(b.getByRole('button',{name:/Resume/})).toHaveCount(0);
  await expect(page.getByRole('note',{name:'Public view'})).toContainText('Not financial advice');
  await expect(page.getByRole('search')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Ask',exact:true})).toHaveCount(0);
  await expect(page.getByRole('link',{name:'API'})).toHaveCount(0);
  await page.keyboard.press('Control+k');
  await page.keyboard.type('btc');
  await expect(page.getByRole('dialog',{name:'Command palette'}).getByRole('option',{name:/^Ask/})).toHaveCount(0);
  await page.keyboard.press('Escape');
  await page.goto('/?public#ask');
  await expect(page.getByRole('heading',{name:'Overview',level:1})).toBeVisible();
  await expect(page.getByRole('button',{name:'Up or down next 4h?'})).toHaveCount(0);
  expect(resumed).toEqual([]);
});

test('account trades explain why each trade was taken and why it lost',async({page})=>{
  await mock(page,[]);
  await page.goto('/#rules');
  const log=page.getByRole('region',{name:'Account trades'});
  await expect(log).toContainText('#12');
  await log.getByText('#12').click();
  await expect(log).toContainText('Price swept the stop below support');
  await expect(log).toContainText('taker_share_15m >= 0.5');
  await expect(log).toContainText('book imbalance');
});

test('the account cards fit a phone screen',async({page})=>{
  await mock(page,[]);
  await page.setViewportSize({width:375,height:812});
  await page.goto('/#rules');
  await expect(page.getByRole('region',{name:'Account Careful'})).toBeVisible({timeout:30000});
  const width=await page.evaluate(()=>({doc:document.documentElement.clientWidth,scroll:document.scrollingElement!.scrollWidth}));
  expect(width.scroll).toBeLessThanOrEqual(width.doc+1);
});

test('the overview shows the four accounts at a glance',async({page})=>{
  await mock(page,[]);
  await page.goto('/#overview');
  const strip=page.getByRole('region',{name:'Paper accounts'});
  await expect(strip).toContainText('Careful');
  await expect(strip).toContainText('12/30');
  await expect(strip).toContainText('Paused');
});

test('model health shows status, calibration and data checks',async({page})=>{
  await mock(page,[]);
  await page.goto('/#rules');
  const h=page.getByRole('region',{name:'Model health'});
  await expect(h).toContainText('Watch',{timeout:30000});
  await expect(h).toContainText('failed the data check');
  await expect(h).toContainText('Is it still right?');
  await h.getByText('Is the data clean?').click();
  await expect(h).toContainText('premium');
  await page.goto('/#overview');
  await expect(page.getByRole('region',{name:'Paper accounts'})).toContainText('Model: Watch');
});

const hist=(extra:object={})=>({status:'failed',passed:false,failed:['beats_break_even','not_luck'],trades:412,win_rate:.47,account_return:-.08,
  max_drawdown:-.12,buy_and_hold:1.9,...extra});
const style=(id:string,name:string,code:string,extra:object={})=>({...acct(id,name,2),style:code,percentile:null,cutoff_score:null,full_risk_score:null,
  expectations:null,watches:[],round_unit:'trades',active:{on:true,reason:'On: watching.'},history_test:hist(),
  rules:[`${name} rule one.`,'Paper money only: nothing here places a real order.'],...extra});
const styles={...accounts,model_hash:null,retired:[{id:'acct-a',status:'RETIRED',retired_at:'2026-11-01T18:00:00+00:00',equity:10000,trades:0}],
  accounts:[
    style('acct-trend','Trend Rider','T1',{position:{side:'long',quantity:.1,entry:80000,stop:74000,target:null,liquidation:40200,leverage:2,
      entry_time:'2026-11-01T00:01:00+00:00',playbook:'T1',style:'T1',reason:'every trend measure points up',
      time_machine:{paths:2000,days:60,stop_first:.31,target_first:0,neither:.69,median_r:.4,history:{available:true,trades:12,win_rate:.5}}}}),
    style('acct-range','Range Trader','R1',{active:{on:false,reason:'Off: the market is trend up, and Range Trader only trades sideways markets.'}}),
    style('acct-breakout','Breakout Hunter','B1',{history_test:hist({status:'passed',passed:true,failed:[]})}),
    style('acct-carry','Funding Collector','C1',{round_unit:'days',in_round:4,leverage:1,risk:null,history_test:{status:'running',note:'The history test (2021-2025) is still running.'},
      position:{kind:'hedge',side:'hedge',entry:80000,entry_time:'2026-11-01T08:00:30+00:00',spot_qty:.075,perp_qty:.075,margin:4000,funding:12.5,rebalances:0,target:null,stop:null,playbook:'C1'}})]};

async function mockStyles(page:Page){
  await page.route('**/api/v1/**',route=>{
    const url=new URL(route.request().url());
    const key=url.pathname.split('/').at(-1)!;
    const values:Record<string,unknown>={accounts:styles,'account-trades':{trades:[]},decisions:{decisions:[]},reviews:{reviews:[]},'model-health':health,
      rules:{rulebook:{hash:'db3d684ff71c',version:2,name:'x'},layers:{},modes:{},playbooks:[],account:null,checklist:[],research:null,provenance_note:'paper',accounts:[]},
      journal:{hits:[],trades:[],triggers:[]},research:{status:'not_run'},candles:[],indicators:{}};
    return route.fulfill({json:{data:values[key]??{},provenance:{source:'fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});
  });
}

test('four style accounts say what they trade, whether they are on, and how their history test went',async({page})=>{
  await mockStyles(page);
  await page.goto('/#rules');
  const t=page.getByRole('region',{name:'Account Trend Rider'});
  await expect(t).toContainText('Trend · holds days to weeks',{timeout:30000});
  await expect(t).toContainText('No fixed target');
  await expect(t).toContainText('Time Machine');
  await expect(t).toContainText('stop first 31%');
  await expect(t).toContainText('History test (2021–2025): failed');
  const r=page.getByRole('region',{name:'Account Range Trader'});
  await expect(r).toContainText('Off');
  await expect(r).toContainText('only trades sideways markets');
  await expect(page.getByRole('region',{name:'Account Breakout Hunter'})).toContainText('History test (2021–2025): passed');
  const c=page.getByRole('region',{name:'Account Funding Collector'});
  await expect(c).toContainText('Day 4 / 30');
  await expect(c).toContainText('Hedge');
  await expect(c).toContainText('$12.50');
  await expect(c).toContainText('still running');
  await expect(page.getByRole('region',{name:'Model health'})).toHaveCount(0);
  await expect(page.getByRole('group',{name:'Account filter'}).getByText('Funding')).toBeVisible();
  await page.goto('/#overview');
  const strip=page.getByRole('region',{name:'Paper accounts'});
  await expect(strip).toContainText('Funding Collector');
  await expect(strip).not.toContainText('Model:');
});

test('the style cards fit a phone screen',async({page})=>{
  await mockStyles(page);
  await page.setViewportSize({width:375,height:812});
  await page.goto('/#rules');
  await expect(page.getByRole('region',{name:'Account Funding Collector'})).toBeVisible({timeout:30000});
  const width=await page.evaluate(()=>({doc:document.documentElement.clientWidth,scroll:document.scrollingElement!.scrollWidth}));
  expect(width.scroll).toBeLessThanOrEqual(width.doc+1);
});

const withBot={...styles,accounts:[style('acct-combined','Combined Bot','X1',{
  active:{on:true,reason:'Sideways: follows range signals (sideways market). Every trade needs a second opinion.'},
  members:{regime:'RANGE',vote:1,leaders:['R1','PB03','PB09','PB10','PB11','B1'],hedge:true,range_trader:'On: the market is sideways.',breakout:'Off: the market is not quiet.'},
  position:{kind:'hedge',side:'hedge',entry:80000,entry_time:'2026-11-02T08:00:30+00:00',spot_qty:.075,perp_qty:.075,margin:4000,funding:3.2,rebalances:0,target:null,stop:null,playbook:'C1'}}),
  ...styles.accounts]};

test('the combined bot says who leads, the veto, and that idle money earns funding',async({page})=>{
  await page.route('**/api/v1/**',route=>{
    const key=new URL(route.request().url()).pathname.split('/').at(-1)!;
    const values:Record<string,unknown>={accounts:withBot,'account-trades':{trades:[]},decisions:{decisions:[]},reviews:{reviews:[]},
      rules:{rulebook:{hash:'x',version:2,name:'x'},layers:{},modes:{},playbooks:[],account:null,checklist:[],research:null,provenance_note:'paper',accounts:[]},
      journal:{hits:[],trades:[],triggers:[]},research:{status:'not_run'},candles:[],indicators:{}};
    return route.fulfill({json:{data:values[key]??{},provenance:{source:'fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});
  });
  await page.goto('/#rules');
  const x=page.getByRole('region',{name:'Account Combined Bot'});
  await expect(x).toContainText('All strategies · one trade at a time',{timeout:30000});
  await expect(x).toContainText('Leads now: Range Trader, PB03, PB09, PB10, PB11, Breakout Hunter');
  await expect(x).toContainText('Daily trend: up (shorts vetoed)');
  await expect(x).toContainText('Idle money is earning funding');
  await expect(x).toContainText('$3.20');
  await expect(page.getByRole('group',{name:'Account filter'}).getByText('Bot',{exact:true})).toBeVisible();
});

test('the trend-led bot shows that only the trend decides the direction',async({page})=>{
  const six={...withBot,accounts:[withBot.accounts[0],style('acct-trend-bot','Trend-Led Bot','X2',{
    active:{on:true,reason:'On: the daily trend is up; it waits for another strategy or support to time a long.'},
    members:{regime:'TREND_UP',vote:1,leaders:['T1'],hedge:false}}),...withBot.accounts.slice(1)]};
  await page.route('**/api/v1/**',route=>{
    const key=new URL(route.request().url()).pathname.split('/').at(-1)!;
    const values:Record<string,unknown>={accounts:six,'account-trades':{trades:[]},decisions:{decisions:[]},reviews:{reviews:[]},
      rules:{rulebook:{hash:'x',version:2,name:'x'},layers:{},modes:{},playbooks:[],account:null,checklist:[],research:null,provenance_note:'paper',accounts:[]},
      journal:{hits:[],trades:[],triggers:[]},research:{status:'not_run'},candles:[],indicators:{}};
    return route.fulfill({json:{data:values[key]??{},provenance:{source:'fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});
  });
  await page.goto('/#rules');
  const x=page.getByRole('region',{name:'Account Trend-Led Bot'});
  await expect(x).toContainText('Trend-led · one trade at a time',{timeout:30000});
  await expect(x).toContainText('Leads now: Trend Rider');
  await expect(x).toContainText('waits for another strategy or support');
  await expect(page.getByRole('group',{name:'Account filter'}).getByText('Trend bot')).toBeVisible();
});
