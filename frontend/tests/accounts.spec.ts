import {test,expect,type Page} from '@playwright/test';

const rules=(lev:number,risk:string)=>[`Takes a confirmed rulebook setup only when its score is at least +0.21 R: the top 10% of setups over 2021–2025.`,
  `Risks ${risk} of equity per trade.`,`Leverage ${lev}× at most; the stop must sit inside the liquidation price.`,'Pauses after 30 trades for a review with the owner.'];
const acct=(id:string,name:string,lev:number,extra:object={})=>({id,name,status:'RUNNING',equity:10000,balance:10000,account_return:0,round:1,in_round:0,
  round_length:30,number:0,leverage:lev,risk:.01,percentile:90,cutoff_score:.21,full_risk_score:.33,daily_brake:.03,halted_day:false,floor_hit:false,
  position:null,pending:false,last:null,expectations:{trades_per_week:1.4,win_rate:.49,mean_r:-.11},review:null,
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

async function mock(page:Page,resumed:string[]){
  await page.route('**/api/v1/**',route=>{
    const url=new URL(route.request().url());
    if(route.request().method()==='POST'){resumed.push(url.pathname);return route.fulfill({json:{data:{account:'acct-b',round:2}}});}
    const key=url.pathname.split('/').at(-1)!;
    const values:Record<string,unknown>={accounts,'account-trades':trades,decisions,reviews,
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
