import {test,expect} from '@playwright/test';

const data={status:'complete',run:'run-20261020',generated:'2026-10-20T15:00:00+00:00',trials:{swing:81,intraday:105},
  passing:[],holdout_evaluated:false,next_test:'2027-04-20',
  families:[{id:'M1',track:'swing',kind:'meta',configurations:18,calls:295,call_rate:.115,hit_rate:.583,wilson_low:.526,
    wilson_high:.638,break_even:.516,mean_net:.0079,deflated_sharpe:.01,pbo:.42,passed:false,failed:['deflated_sharpe','low_overfitting']}],
  forward:{liquidations:{rows:12,first:'2026-10-20T14:00:00+00:00',last:'2026-10-20T15:00:00+00:00'},
    depth:{rows:0,first:null,last:null}}};

test('strategy research shows hit rate with call rate, the verdict and forward data collection',async({page})=>{
  await page.route('**/api/v1/**',route=>{const key=new URL(route.request().url()).pathname.split('/').at(-1)!;
    const values:Record<string,unknown>={candles:[],indicators:{},positioning:[],sentiment:[],coverage:{release_ready:false},
      predictions:{action:'NO_TRADE',reasons:['WEAK_FORECAST']},'strategy-research':data};
    return route.fulfill({json:{data:values[key]??{},provenance:{source:'fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});});
  await page.goto('/');await page.getByRole('button',{name:'Prediction',exact:true}).click();
  const panel=page.getByRole('region',{name:'Strategy research'});
  await expect(panel).toContainText('No strategy passed');
  await expect(panel).toContainText('58.3% (52.6–63.8%)');
  await expect(panel).toContainText('11.5% of days');
  await expect(panel).toContainText('51.6%');
  await expect(panel).toContainText('deflated sharpe · low overfitting');
  await expect(panel).toContainText('186 configurations');
  await expect(panel).toContainText('12 minutes with liquidations');
  await expect(panel).toContainText('not recording yet');
  await expect(panel).toContainText('2027-04-20');
  await expect(panel).toContainText('Holdout year untouched');
});
