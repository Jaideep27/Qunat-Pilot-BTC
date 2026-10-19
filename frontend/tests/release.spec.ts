import {test,expect} from '@playwright/test';

test('trained release renders real market, prediction, research and paper evidence',async({page})=>{
  test.skip(!process.env.QUANTPILOT_RELEASE_SMOKE,'Requires local audited data, frozen model and API on port 8000');
  test.setTimeout(120000);
  const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.routeWebSocket(/fstream\.binance\.com/,socket=>socket.close());
  await page.goto('/');
  await expect(page.getByRole('img',{name:'BTCUSDT five-minute candlestick and volume chart'})).toBeVisible({timeout:60000});
  await expect(page.getByText('INTERFACE PREVIEW',{exact:false})).toHaveCount(0);
  await page.screenshot({path:'test-results/release-market.png',fullPage:true});
  const predictionResponse=page.waitForResponse(response=>response.url().endsWith('/api/v1/predictions')&&response.status()===200);
  await page.getByRole('button',{name:'Prediction',exact:true}).click();
  const decision=(await (await predictionResponse).json()).data;
  await expect(page.getByRole('heading',{name:'Decision gates'})).toBeVisible();
  const reasons=decision.reasons.length?decision.reasons:['ALL_GATES_PASSED'];
  await expect(page.locator('.gate-list strong')).toHaveText(reasons.map((reason:string)=>reason.replaceAll('_',' ')),{timeout:60000});
  await expect(page.locator('.stat').filter({hasText:'POLICY OUTCOME'})).toContainText(decision.action.replaceAll('_',' '));
  await page.screenshot({path:'test-results/release-prediction.png',fullPage:true});
  await page.getByRole('button',{name:'Research',exact:true}).click();
  await expect(page.getByText('ELIGIBLE WITH QUARANTINE',{exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Holdout equity · full'})).toBeVisible();
  await expect(page.getByRole('button',{name:'full',exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Probability calibration'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Holdout trade ledger'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Drawdown: hourly equity samples'})).toBeVisible();
  await page.screenshot({path:'test-results/release-research.png',fullPage:true});
  const portfolioResponse=page.waitForResponse(response=>response.url().endsWith('/api/v1/portfolio')&&response.status()===200);
  await page.getByRole('button',{name:'Paper portfolio',exact:true}).click();
  const portfolio=(await (await portfolioResponse).json()).data;
  await expect(page.getByRole('heading',{name:'Execution ledger'})).toBeVisible();
  if(portfolio.fills.length){
    const ledger=page.locator('.panel').filter({has:page.getByRole('heading',{name:'Execution ledger',exact:true})});
    await expect(ledger.locator('tbody tr')).toHaveCount(portfolio.fills.length);
  }else await expect(page.getByText('No fills recorded')).toBeVisible();
  await page.screenshot({path:'test-results/release-paper.png',fullPage:true});
  expect(errors).toEqual([]);
});
