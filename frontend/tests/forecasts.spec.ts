import {test,expect} from '@playwright/test';

const forecast=(horizon:string,extra:object)=>({cutoff:'2026-10-20T09:00:00+00:00',horizon,model_version:'abc123',
  base_rate:.51,regime:'ranging',drop5_risk:.07,volatility:{ewma:.004,garch:.005,model:'garch',value:.005},
  champion:{group:'price',training:'expanding',training_end:'2026-10-18T00:00:00+00:00'},...extra});
const data={latest:[forecast('1h',{status:'forecast',range_status:'model',probability_up:.53,return_q10:-.006,return_q50:.0002,return_q90:.0061}),
    forecast('24h',{status:'no_validated_skill',range_status:'unconditional',probability_up:.51,return_q10:-.03,return_q50:.001,return_q90:.031})],
  forward:{'1h':{scored:40,pending:12,brier:.2493,constant_brier:.2501,interval_coverage_80:.79}},
  champions:{'1h':{group:'price',training:'expanding',validation_skill:.0004,test_skill:.0002,configurations_tried:10},
    '24h':{group:'constant',training:null,validation_skill:0,test_skill:0,configurations_tried:10}},
  backtest_overfitting:{'1h':{pbo:.25}},status:'shadow_only_never_trades'};

test('research forecasts show ranges, risk and an honest no-skill label',async({page})=>{
  await page.route('**/api/v1/**',route=>{const key=new URL(route.request().url()).pathname.split('/').at(-1)!;
    const values:Record<string,unknown>={candles:[],indicators:{},positioning:[],sentiment:[],coverage:{release_ready:false},
      predictions:{action:'NO_TRADE',reasons:['WEAK_FORECAST']},forecasts:data};
    return route.fulfill({json:{data:values[key]??{},provenance:{source:'fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});});
  await page.goto('/');await page.getByRole('button',{name:'Research',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Research',level:1})).toBeVisible({timeout:30000});
  const panel=page.getByRole('region',{name:'Research forecasts'});
  await expect(panel).toContainText('53.0%');
  await expect(panel).toContainText('-0.60% to +0.61%');
  await expect(panel).toContainText('No validated skill');
  await expect(panel).toContainText('7.0%');
  await expect(panel).toContainText('40 scored');
  await expect(panel).toContainText('PBO 0.25');
  await expect(panel).toContainText('never trades');
  await expect(panel).toContainText('GARCH champion');
  await expect(panel).toContainText('range model');
  await expect(panel).toContainText('unconditional range');
});
