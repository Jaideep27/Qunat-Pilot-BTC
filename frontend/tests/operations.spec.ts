import {test,expect,type Page} from '@playwright/test';

function report(){return {ready:false,checks:{database:{ok:true,status:'connected'},paper_worker:{ok:false,status:'failed'}},
  worker:{status:'failed',last_checkpoint:'2026-10-19T10:00:00Z',next_scheduled_at:'2026-10-19T10:17:00Z',
    duration_p95_seconds:1.5 as number|null,latency_sample_count:3,latency_sample_scope:'Latest 100 recorded runs',
    run_totals:{succeeded:3,failed:1},runs:[{id:'fixture-run',started_at:'2026-10-19T10:02:00Z',finished_at:'2026-10-19T10:02:02Z',
      status:'failed',stage:'execution_quote',duration_seconds:2,error_type:'TimeoutError',details:{}}]},
  forward_evidence:{forward_decisions:2301,forward_fills:3,replayed_fills:1,observed_span_hours:25}};}

async function setup(page:Page,options={available:true,legacy:false}){
  const values:Record<string,unknown>={candles:[],indicators:{},positioning:[],sentiment:[],
    predictions:{action:'NO_TRADE',reasons:['MODEL_RELEASE_BLOCKED']},coverage:{release_ready:false},
    backtests:{status:'blocked',comparisons:[],benchmarks:{}},
    portfolio:{equity:10000,position:null,fills:[],decisions:[],paused:false}};
  await page.route('**/api/v1/**',route=>{
    const key=new URL(route.request().url()).pathname.split('/').at(-1)!;
    if(key==='operations'&&!options.available)return route.fulfill({status:503,json:{detail:'Fixture API unavailable'}});
    const operations=report();
    if(options.legacy){
      operations.worker.status='healthy';operations.worker.runs=[];operations.worker.run_totals={succeeded:0,failed:0};
      operations.worker.duration_p95_seconds=null;operations.worker.latency_sample_count=0;
      operations.checks.paper_worker={ok:true,status:'healthy'};
    }
    return route.fulfill({json:{data:key==='operations'?operations:values[key],
      provenance:{source:'Browser operations test fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});
  });
  await page.goto('/');await page.getByRole('button',{name:'System',exact:true}).click();
}

test('worker failure remains visible despite a recent checkpoint and full-ledger totals exceed the UI cap',async({page})=>{
  await setup(page);
  const monitor=page.getByRole('region',{name:'Paper worker monitoring'});
  await expect(monitor.getByText('NEEDS ATTENTION',{exact:true})).toBeVisible();
  await expect(monitor.getByText('FAILED',{exact:true})).toBeVisible();
  await expect(monitor.getByText('2,301',{exact:true})).toBeVisible();
  await expect(monitor.getByRole('cell',{name:'execution quote',exact:true})).toBeVisible();
  await expect(monitor.getByRole('cell',{name:'TimeoutError',exact:true})).toBeVisible();
  await expect(monitor).toContainText('It is not an uptime percentage');
  await page.screenshot({path:'test-results/worker-monitoring.png',fullPage:true});
});

test('monitoring outages are retryable and do not hide the paper ledger',async({page})=>{
  const options={available:false,legacy:false};await setup(page,options);
  await expect(page.getByText('MONITOR UNAVAILABLE',{exact:true})).toBeVisible();
  options.available=true;await page.getByRole('button',{name:'Retry monitor',exact:true}).click();
  await expect(page.getByRole('table',{name:'Recorded paper worker runs'})).toBeVisible();
  options.available=false;await page.getByRole('button',{name:'Paper',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Execution ledger'})).toBeVisible();
});

test('legacy checkpoints do not fabricate monitored durations or uptime',async({page})=>{
  await setup(page,{available:true,legacy:true});
  await expect(page.getByText('Awaiting measured runs',{exact:true})).toBeVisible();
  await expect(page.getByText('Run instrumentation has not started.',{exact:false})).toBeVisible();
  await expect(page.getByText('HEALTHY',{exact:true})).toBeVisible();
});
