import {test,expect,type Page,type WebSocketRoute} from '@playwright/test';
import {parseKline} from '../src/liveFeed';

const seconds:Record<string,number>={'5m':300,'15m':900,'30m':1800,'1h':3600,'4h':14400,'1d':86400,'1w':604800};
function message(timeframe='5m',close=101,eventAt=Date.now(),closed=false){
  const duration=seconds[timeframe]*1000,start=Math.floor(eventAt/duration)*duration;
  return {e:'kline',E:eventAt,s:'BTCUSDT',k:{s:'BTCUSDT',i:timeframe,t:start,T:start+duration-1,
    o:'100',h:String(Math.max(102,close+1)),l:String(Math.min(99,close-1)),c:String(close),v:'12',x:closed}};
}

async function fixture(page:Page){
  const state={stored:100},sockets:WebSocketRoute[]=[];
  await page.route('**/api/v1/**',route=>{
    const url=new URL(route.request().url()),resource=url.pathname.split('/api/v1/')[1],tf=url.searchParams.get('timeframe')??'5m';
    const duration=seconds[tf]*1000,end=Math.floor(Date.now()/duration)*duration-(tf==='5m'?0:duration);
    const bars=Array.from({length:100},(_,i)=>({event_time:new Date(end-(99-i)*duration).toISOString(),
      available_at:new Date(end-(98-i)*duration).toISOString(),received_at:new Date().toISOString(),
      open:100,high:Math.max(102,state.stored+1),low:99,close:i===99?state.stored:100,volume:10,complete:i<99||tf!=='5m',source:'Browser stream test fixture'}));
    const data=resource==='market/candles'||resource==='candles'?bars:resource==='market/studies'?
      {catalogue:[{id:'EMA 21',label:'EMA 21',pane:'overlay'},{id:'rsi_14',label:'RSI 14',pane:'oscillator'}],series:{}}:
      resource==='market/storage'?{engine:'postgresql',markets:[{market:'BTCUSDT',rows:100,first:bars[0].event_time,last:bars.at(-1)!.event_time}],
        feeds:[{id:'live:collector',updated_at:new Date().toISOString(),state:{status:'connected'}}]}:{release_ready:false};
    return route.fulfill({json:{data,provenance:{source:'Browser stream test fixture',freshness:'fresh',observed_at:new Date().toISOString()},model_version:null,config_version:'fixture'}});
  });
  await page.routeWebSocket(/fstream\.binance\.com/,socket=>{sockets.push(socket)});
  return {state,sockets};
}

test('stream messages reject other markets, stale events and invalid OHLC',()=>{
  const now=Date.now(),valid=message('5m',101,now);
  expect(parseKline(JSON.stringify(valid),'5m',now)?.bar.close).toBe(101);
  expect(parseKline(JSON.stringify({data:valid}),'5m',now)?.bar.volume).toBe(12);
  expect(parseKline(JSON.stringify({...valid,s:'ETHUSDT'}),'5m',now)).toBeNull();
  expect(parseKline(JSON.stringify(valid),'1h',now)).toBeNull();
  expect(parseKline(JSON.stringify({...valid,k:{...valid.k,h:'99'}}),'5m',now)).toBeNull();
  expect(parseKline(JSON.stringify({...valid,E:now-31000}),'5m',now)).toBeNull();
  expect(parseKline('invalid json','5m',now)).toBeNull();
});

test('stream updates candles immediately without recreating the chart or claiming a database write',async({page})=>{
  const {sockets}=await fixture(page),errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/#chart');
  await expect(page.getByText('Chart ready',{exact:true})).toBeVisible();
  await expect.poll(()=>sockets.length).toBeGreaterThanOrEqual(1);
  const socket=sockets.at(-1)!; // React StrictMode closes the first development mount.
  expect(socket.url()).toContain('/market/ws/btcusdt@kline_5m');
  const canvas=page.locator('.workspace-canvas canvas').first();
  await canvas.evaluate(node=>node.setAttribute('data-continuous-chart','yes'));
  await page.evaluate(()=>{
    const node=document.querySelector('[aria-label="Last live price"]')!;
    const values:string[]=[];(window as unknown as {livePrices:string[]}).livePrices=values;
    new MutationObserver(()=>values.push(node.textContent??'')).observe(node,{childList:true,subtree:true,characterData:true});
  });
  const now=Date.now();
  socket.send(JSON.stringify(message('5m',100.25,now)));
  await expect(page.getByLabel('Last live price')).toHaveText('Live 100.25');
  socket.send(JSON.stringify(message('5m',100.5,now+1,true)));
  await expect(page.getByLabel('Last live price')).toHaveText('Live 100.50');
  socket.send(JSON.stringify(message('5m',105,now+2,false))); // Cannot reopen a closed bar.
  socket.send(JSON.stringify(message('5m',98,now-1,true))); // Cannot regress the exchange event time.
  socket.send(JSON.stringify(message('5m',100.75,now+3,true)));
  await expect(page.getByLabel('Live stream status')).toHaveAttribute('data-event-time',String(now+3));
  await expect(page.getByLabel('Last live price')).toHaveText('Live 100.75');
  await expect(page.getByLabel('Last stored price')).toHaveText('Stored 100.00');
  await expect(canvas).toHaveAttribute('data-continuous-chart','yes');
  const observed=await page.evaluate(()=>(window as unknown as {livePrices:string[]}).livePrices);
  expect(observed).not.toContain('Live 105.00');expect(observed).not.toContain('Live 98.00');
  expect(errors).toEqual([]);
});

test('disconnection uses persisted prices and reconnects; timeframe changes subscribe to matching candles',async({page})=>{
  await page.clock.install({time:new Date()});
  const {state,sockets}=await fixture(page);
  await page.goto('/#chart');
  await expect(page.getByText('Chart ready',{exact:true})).toBeVisible();
  await expect.poll(()=>sockets.length).toBeGreaterThanOrEqual(1);
  const initialConnections=sockets.length;
  const now=Date.now();sockets.at(-1)!.send(JSON.stringify(message('5m',101,now)));
  await expect(page.getByLabel('Live stream status')).toContainText('connected');
  state.stored=105;sockets.at(-1)!.close();
  await expect(page.getByLabel('Live stream status')).toContainText('database fallback');
  await page.clock.fastForward(6000);
  await expect.poll(()=>sockets.length).toBe(initialConnections+1);
  await expect(page.getByLabel('Last stored price')).toHaveText('Stored 105.00');
  sockets.at(-1)!.send(JSON.stringify(message('5m',107,now+7000)));
  await expect(page.getByLabel('Last live price')).toHaveText('Live 107.00');
  await page.getByRole('button',{name:'1h',exact:true}).click();
  await expect.poll(()=>sockets.length).toBe(initialConnections+2);
  expect(sockets.at(-1)!.url()).toContain('kline_1h');
  sockets.at(-1)!.send(JSON.stringify(message('1h',108,now+8000)));
  await expect(page.getByLabel('Last live price')).toHaveText('Live 108.00');
  await expect(page.getByText('BTCUSDT / 1h',{exact:true})).toBeVisible();
});

test('an open but silent socket becomes stale and reconnects',async({page})=>{
  await page.clock.install({time:new Date()});
  const {sockets}=await fixture(page);
  await page.goto('/#chart');
  await expect(page.getByText('Chart ready',{exact:true})).toBeVisible();
  await expect.poll(()=>sockets.length).toBeGreaterThanOrEqual(1);
  const connections=sockets.length,now=Date.now();
  sockets.at(-1)!.send(JSON.stringify(message('5m',101,now)));
  await expect(page.getByLabel('Live stream status')).toHaveText('Live stream connected');
  await page.clock.fastForward(12000);
  await expect(page.getByLabel('Live stream status')).toContainText('database fallback');
  await page.clock.fastForward(1100);
  await expect.poll(()=>sockets.length).toBeGreaterThan(connections);
  sockets.at(-1)!.send(JSON.stringify(message('5m',102,now+14000)));
  await expect(page.getByLabel('Last live price')).toHaveText('Live 102.00');
  await expect(page.getByLabel('Live stream status')).toHaveText('Live stream connected');
});
