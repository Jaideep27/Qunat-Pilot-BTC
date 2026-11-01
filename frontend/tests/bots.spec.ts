import {test,expect,type Page} from '@playwright/test';

const report={trades:3,wins:1,win_rate:.33,account_return:-.004,avg_win:80,avg_loss:-60,profit_factor:.67,max_drawdown:-.01,longest_losing_streak:2,hold_return:.02};
const hist=(extra:object={})=>({status:'failed',passed:false,failed:['beats_break_even','not_luck'],trades:412,win_rate:.47,account_return:-.08,
  max_drawdown:-.12,buy_and_hold:1.9,...extra});
const bot=(id:string,name:string,style:string,extra:object={})=>({id,name,style,status:'RUNNING',equity:10000,balance:10000,account_return:0,round:1,
  in_round:0,round_length:30,round_unit:'trades',number:0,halted_day:false,floor_hit:false,position:null,pending:false,last:null,review:null,
  report,active:{on:true,reason:`${name} is watching.`},history_test:hist(),members:null,
  rules:[`${name} rule one.`,'Paper money only: nothing here places a real order.'],...extra});
const accounts={accounts:[
  bot('acct-combined','Combined Bot','X1',{active:{on:true,reason:'Sideways: follows range signals (sideways market). Every trade needs a second opinion.'},
    members:{regime:'RANGE',vote:1,leaders:['R1','PB03','PB09','PB10','PB11','B1'],hedge:true},
    position:{kind:'hedge',side:'hedge',entry:80000,entry_time:'2026-11-02T08:00:30+00:00',spot_qty:.075,perp_qty:.075,margin:4000,funding:3.2,rebalances:0,target:null,stop:null,playbook:'C1'}}),
  bot('acct-trend-bot','Trend-Led Bot','X2',{active:{on:false,reason:'Off: no clear daily trend, so it stays out.'},members:{regime:'RANGE',vote:0,leaders:['T1'],hedge:false}}),
  bot('acct-trend','Trend Rider','T1',{equity:10123.4,account_return:.01234,
    position:{side:'long',quantity:.1,entry:80000,stop:74000,target:null,liquidation:40200,leverage:2,entry_time:'2026-11-01T00:01:00+00:00',playbook:'T1',
      reason:'every trend measure points up',time_machine:{paths:2000,days:60,stop_first:.31,target_first:0,neither:.69,median_r:.4}}}),
  bot('acct-range','Range Trader','R1',{status:'PAUSED_FOR_REVIEW',in_round:30,review:{id:'acct-range:round-1',round:1,status:'ready'}}),
  bot('acct-breakout','Breakout Hunter','B1',{history_test:hist({status:'passed',passed:true,failed:[]})}),
  bot('acct-carry','Funding Collector','C1',{round_unit:'days',in_round:4,equity:9987.4,account_return:-.00126,history_test:{status:'running'},
    position:{kind:'hedge',side:'hedge',entry:80000,entry_time:'2026-11-01T08:00:30+00:00',spot_qty:.075,perp_qty:.075,margin:4000,funding:12.5,rebalances:0,target:null,stop:null,playbook:'C1'}})]};
const trades={trades:[{id:'acct-trend:T1:t',account:'acct-trend',round:1,number:1,playbook:'T1',side:'long',entry_time:'2026-10-30T00:01:00+00:00',
  exit_time:'2026-10-31T06:00:00+00:00',net:-41.2,reason:'STOP',diagnosis:'FAIL_STOP_HUNT',payload:{entry:80000,exit:79200},
  explanation:{why:'Price swept the stop before turning up.',lesson:'Wide stops still get hit in fast drops.'}}]};
const decisions={decisions:[{id:'acct-trend:T1:t',account:'acct-trend',trade_id:'acct-trend:T1:t',payload:{reason:'every trend measure points up',sizing:{risk_amount:100}}}]};
const reviews={reviews:[{id:'acct-range:round-1',account:'acct-range',round:1,status:'ready',created_at:'2026-11-01T08:00:00+00:00',payload:{
  numbers:{trades:30,wins:11,win_rate:.367,total_net:-350,max_drawdown:-.06,hold_return:.01},
  summary:{where_it_went_wrong:'Most losses came when the range broke.',why:'Breaks ran further than the stop.',suggestions:['Skip ranges right before news.']}}}]};

async function mock(page:Page,posted:string[]=[]){
  await page.route('**/api/v1/**',route=>{
    const url=new URL(route.request().url());
    if(route.request().method()==='POST'){posted.push(url.pathname);return route.fulfill({json:{data:{account:'acct-range',round:2}}});}
    const key=url.pathname.split('/').at(-1)!;
    const values:Record<string,unknown>={accounts,'account-trades':trades,decisions,reviews,candles:[],indicators:{},
      forecasts:{latest:[{horizon:'4h',status:'no_validated_skill',probability_up:.51,return_q10:-.004,return_q90:.007,regime:'ranging'}],forward:{}},
      macro:{upcoming:{jobs:{id:'j',label:'Jobs report',scheduled_at:'2099-11-06T13:30:00+00:00'}},recent:[]},news:{items:[]}};
    return route.fulfill({json:{data:values[key]??{},provenance:{source:'fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});
  });
}

test('home lists every bot with its money and what it is doing, and a tap opens it',async({page})=>{
  await mock(page);
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'Home',level:1})).toBeVisible({timeout:30000});
  const list=page.getByRole('region',{name:'Your bots'});
  for(const name of ['Combined Bot','Trend-Led Bot','Trend Rider','Range Trader','Breakout Hunter','Funding Collector'])await expect(list).toContainText(name);
  await expect(list).toContainText('$10,123.40');
  await expect(list).toContainText('Paused for review');
  await expect(list).toContainText('Earning funding');
  await expect(page.getByRole('region',{name:'Market now'})).toContainText('about a coin flip');
  await list.getByRole('button',{name:'Open Trend Rider'}).click();
  await expect(page.getByRole('heading',{name:'Bots',level:1})).toBeVisible();
  const card=page.getByRole('region',{name:'Account Trend Rider'});
  await expect(card).toContainText('Long 0.1 BTC');
  await expect(card).toContainText('no fixed target');
  await expect(card).toContainText('stop first 31%');
});

test('the bots page shows one bot at a time with its rules and history folded away',async({page})=>{
  await mock(page);
  await page.goto('/#bots');
  const picker=page.getByRole('group',{name:'Choose a bot'});
  await picker.getByRole('button',{name:'Combined'}).click();
  const x=page.getByRole('region',{name:'Account Combined Bot'});
  await expect(x).toContainText('Leads now: Range Trader, rulebook setups, Breakout Hunter');
  await expect(x).toContainText('idle money is earning funding');
  await expect(x).toContainText('$3.20');
  await expect(page.getByRole('region',{name:'Account Trend Rider'})).toHaveCount(0);
  await x.getByText('How it trades').click();
  await expect(x).toContainText('History test (2021–2025): failed');
  await picker.getByRole('button',{name:'Funding'}).click();
  const c=page.getByRole('region',{name:'Account Funding Collector'});
  await expect(c).toContainText('Day 4 of 30');
  await expect(c).toContainText('$12.50');
});

test('a paused bot shows its review and resumes on request',async({page})=>{
  const posted:string[]=[];
  await mock(page,posted);
  await page.goto('/#bots');
  await page.getByRole('group',{name:'Choose a bot'}).getByRole('button',{name:'Range'}).click();
  const r=page.getByRole('region',{name:'Account Range Trader'});
  await expect(r).toContainText('Paused for review');
  await r.getByRole('button',{name:'Open review'}).click();
  await expect(page.getByRole('region',{name:'Round review'})).toContainText('Most losses came when the range broke.');
  await r.getByRole('button',{name:'Resume Range Trader'}).click();
  await expect.poll(()=>posted).toEqual(['/api/v1/rules/accounts/acct-range/resume']);
});

test('each trade says why it was taken and why it won or lost',async({page})=>{
  await mock(page);
  await page.goto('/#bots');
  await page.getByRole('group',{name:'Choose a bot'}).getByRole('button',{name:'Trend',exact:true}).click();
  const log=page.getByRole('region',{name:'Account trades'});
  await log.getByText(/^Long ·/).click();
  await expect(log).toContainText('every trend measure points up');
  await expect(log).toContainText('Price swept the stop before turning up.');
});

test('the public view is read-only: no resume, no Ask, and it says so',async({page})=>{
  const posted:string[]=[];
  await mock(page,posted);
  await page.goto('/?public#bots');
  await page.getByRole('group',{name:'Choose a bot'}).getByRole('button',{name:'Range'}).click();
  const r=page.getByRole('region',{name:'Account Range Trader'});
  await expect(r).toContainText('Paused for review');
  await expect(r.getByRole('button',{name:/Resume/})).toHaveCount(0);
  await expect(page.getByRole('note',{name:'Public view'})).toContainText('Not financial advice');
  await expect(page.getByRole('search')).toHaveCount(0);
  await page.goto('/?public#more');
  await expect(page.getByRole('list',{name:'More pages'})).not.toContainText('Ask');
  expect(posted).toEqual([]);
});

test('more lists the other pages and opens them',async({page})=>{
  await mock(page);
  await page.goto('/#more');
  const more=page.getByRole('list',{name:'More pages'});
  for(const name of ['Ask','News','Research','Rule engine','System'])await expect(more).toContainText(name);
  await more.getByRole('button',{name:/Research/}).click();
  await expect(page).toHaveURL(/#research$/);
  await expect(page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'More'})).toHaveAttribute('aria-current','page');
});

test('home and bots fit a phone screen with four tabs',async({page})=>{
  await mock(page);
  await page.setViewportSize({width:375,height:812});
  for(const hash of ['#overview','#bots','#more']){
    await page.goto('/'+hash);
    await expect(page.getByRole('heading',{level:1})).toBeVisible({timeout:30000});
    const width=await page.evaluate(()=>({doc:document.documentElement.clientWidth,scroll:document.scrollingElement!.scrollWidth}));
    expect(width.scroll,hash).toBeLessThanOrEqual(width.doc+1);
  }
  const nav=page.getByRole('navigation',{name:'Main navigation'});
  for(const tab of ['Home','Bots','Chart','More'])await expect(nav.getByRole('button',{name:tab})).toBeVisible();
});
