import {test,expect} from '@playwright/test';

const playbook=(id:string,name:string,side:string,extra:object={})=>({id,name,family:'dump',side,source:['R11'],thesis:'A flush into support.',priority:1,
  shadow_only:false,trigger:{any:['DUMP'],within_bars:6,min_bars:1},regimes:['RANGE'],conditions:['trigger.oi_change <= -0.005','taker_share_15m >= 0.5'],
  abort:['close_15m < trigger.low'],stop:{anchor:'trigger.low',buffer_atr:.25},target:{candidates:['vwap'],cap:'band4_high',fallback_r:2},
  time_stop_hours:12,breakeven_at_r:1,may_flip:false,...extra});
const status={rulebook:{hash:'3ea0f47e52c1',version:1,name:'QuantPilot event playbooks'},layers:{ml:'shadow',news:'shadow',learner:'shadow',manage_rl:'shadow'},
  modes:{ml:{PB01:'live'}},provenance_note:'paper only',
  playbooks:[playbook('PB01','Flush rebound','long'),playbook('PB13L','News-shock continuation (long)','long',{shadow_only:true})],
  account:{equity:10042.5,cash:10042.5,balance:10000,account_return:.00425,position:{side:1,quantity:.05,entry:80000,stop:79000,target:82000,
    entry_time:'2026-10-21T10:01:00+00:00',max_favorable:.004,max_adverse:-.002,plan:{playbook:'PB01'}},pending:null,trades:3,halted_day:false,
    drawdown_halt:false,paused:false,last_bar:'2026-10-21T10:30:00+00:00',last_decision:'2026-10-21T10:30:00+00:00',created:'2026-10-21T09:00:00+00:00'},
  checklist:[{playbook:'PB01',status:'TAKEN',passed:7,total:7,missing:[],trigger:'DUMP',time:'2026-10-21T10:00:00+00:00',history:{setups:254,went_their_way_1h:{n:254,share:.51,low:.45,high:.57},went_their_way_4h:{n:254,share:.48,low:.42,high:.54},median_move_4h:-.001,plan:{n:250,win_rate:.37,mean_r:-.42}},layers:{learner:{verdict:'skip',mode:'shadow'}}},
    {playbook:'PB02',status:'NOT_CONFIRMED',passed:3,total:5,missing:['liq_z_15m'],trigger:'DUMP',time:'2026-10-21T10:00:00+00:00'}],research:null};
const journal={hits:[{id:'PB01:x',time:'2026-10-21T10:00:00+00:00',playbook:'PB01',side:'long',status:'TAKEN',diagnosis:'PASS_CLEAN',
    payload:{plan:{reward_risk:1.8,stop:79000,target:82000}}}],
  trades:[{id:'t1',playbook:'PB01',side:'long',entry_time:'2026-10-20T10:00:00+00:00',exit_time:'2026-10-20T12:00:00+00:00',net:-12.5,
    reason:'STOP',diagnosis:'FAIL_STOP_HUNT',payload:{entry:80000,exit:79000,quantity:.05,diagnosis:{why:'Direction was right'}}}],
  triggers:[{id:'DUMP:1',time:'2026-10-21T09:55:00+00:00',type:'DUMP',payload:{direction:-1,strength:-6.2}}]};
const research={status:'complete',run:'research-20261021',trials:216,hits:5000,passing:[],development:['2021-10-20','2025-10-20'],
  playbooks:{PB01:{name:'Flush rebound',side:'long',report:{calls:140,hit_rate:.41,wilson_low:.33,break_even:.45,mean_net:-.0012},gate:{passed:false,criteria:{}},hits:300,tradeable:150}},
  combined:{report:{calls:400,hit_rate:.38,wilson_low:.33,break_even:.4,mean_net:-.001},gate:{passed:false},account:{account_return:-.083,trades:400,max_drawdown_closed:-.12}},
  layers:{ml:{summary:'kept in shadow for every playbook'}}};

test('the rules page shows the live checklist, playbook verdicts, the journal and layer modes',async({page})=>{
  await page.route('**/api/v1/**',route=>{const key=new URL(route.request().url()).pathname.split('/').at(-1)!;
    const values:Record<string,unknown>={rules:status,journal,research,candles:[],indicators:{}};
    return route.fulfill({json:{data:values[key]??{},provenance:{source:'fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});});
  await page.goto('/');await page.getByRole('button',{name:'Rules',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Rule engine',level:1})).toBeVisible({timeout:30000});
  await expect(page.getByRole('note')).toContainText('3ea0f47e52c1');
  const checklist=page.getByRole('region',{name:'Live checklist'});
  await expect(checklist).toContainText('7/7');
  await expect(checklist).toContainText('not confirmed');
  await expect(checklist).toContainText('liq_z_15m');
  await expect(checklist).toContainText('48% went its way in 4h · plan won 37%, -0.42R (n=254)');
  await expect(checklist).toContainText('skip (shadow)');
  const books=page.getByRole('region',{name:'Playbooks'});
  await expect(books).toContainText('Flush rebound');
  await expect(books).toContainText('41.0%');
  await expect(books).toContainText('failed');
  await expect(books).toContainText('shadow');
  await expect(page.getByRole('region',{name:'Open position'})).toContainText('$80,000.00');
  const log=page.getByRole('region',{name:'Journal'});
  await expect(log).toContainText('pass clean');
  await log.getByRole('button',{name:'Trades',exact:true}).click();
  await expect(log).toContainText('fail stop hunt');
  await log.getByRole('button',{name:'Triggers',exact:true}).click();
  await expect(log).toContainText('move -6.2σ');
  await expect(page.getByRole('region',{name:'Decision layers'})).toContainText('Live for PB01');
  const test=page.getByRole('region',{name:'Historical test'});
  await expect(test).toContainText('-8.30%');
  await expect(test).toContainText('None passed');
});
