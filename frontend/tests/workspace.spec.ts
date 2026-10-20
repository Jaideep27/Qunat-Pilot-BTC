import {test,expect} from '@playwright/test';

test('PostgreSQL workspace switches timeframes, studies and persists drawings',async({page})=>{
  test.skip(!process.env.QUANTPILOT_RELEASE_SMOKE,'Requires the local PostgreSQL market database and API');
  test.setTimeout(120000);
  await page.setViewportSize({width:1440,height:1000});
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/#chart');
  await expect(page.getByRole('heading',{name:'Chart',level:1})).toBeVisible();
  await expect(page.getByRole('img',{name:'Interactive BTCUSDT chart'})).toBeVisible();
  await expect(page.getByText('postgresql',{exact:true})).toBeVisible({timeout:60000});
  await expect(page.getByLabel('Chart market',{exact:true})).toHaveText('BTCUSDT');
  await expect(page.getByRole('combobox',{name:'Chart market'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:/Indicators \(/})).toBeVisible();
  await page.getByRole('button',{name:/Indicators \(/}).click();
  await page.getByRole('textbox',{name:'Search indicators'}).fill('Bollinger');
  await page.getByRole('checkbox',{name:/Bollinger upper/}).check();
  await page.getByRole('button',{name:'Close',exact:true}).click();
  await page.getByRole('button',{name:'1h',exact:true}).click();
  await expect(page.getByText('BTCUSDT / 1h',{exact:true})).toBeVisible();
  await expect(page.getByText('Chart ready',{exact:true})).toBeVisible({timeout:30000});
  await page.getByRole('button',{name:'Horizontal line',exact:true}).click();
  const chart=page.getByRole('img',{name:'Interactive BTCUSDT chart'});
  await expect(page.getByText('Click on the price pane to draw.')).toBeVisible();
  await page.screenshot({path:'test-results/workspace-before-draw.png',fullPage:true});
  await chart.click({position:{x:180,y:150}});
  await expect(page.getByRole('heading',{name:'Saved drawings (1)'})).toBeVisible();
  await page.reload();
  // Drawings belong to their market/timeframe, so select 1h again after reload.
  await page.getByRole('button',{name:'1h',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Saved drawings (1)'})).toBeVisible({timeout:60000});
  await expect(page.getByText('Chart ready',{exact:true})).toBeVisible({timeout:60000});
  await expect(page.getByText('postgresql',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Clear drawings',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Saved drawings (0)'})).toBeVisible();
  await page.screenshot({path:'test-results/postgres-workspace.png',fullPage:true});
  expect(errors).toEqual([]);
});
