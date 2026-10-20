// Actual exchange/browser/PostgreSQL verification. No fixture prices are written.
const fs=require('node:fs'),path=require('node:path');
process.chdir(path.resolve(__dirname,'..'));
const {chromium}=require(path.resolve('frontend/node_modules/@playwright/test'));
(async()=>{
  const browser=await chromium.launch({channel:process.env.QUANTPILOT_BROWSER_CHANNEL||'chrome',headless:true});
  const report={verified_at:new Date().toISOString(),source:'Actual Binance stream and actual PostgreSQL reads; no mocked messages',stream_verified:false,database_advanced:false};
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    report.socket_errors=[];report.sample_frames=[];
    page.on('websocket',socket=>{
      if(!socket.url().includes('fstream.binance.com'))return;
      socket.on('socketerror',error=>report.socket_errors.push(String(error)));
      socket.on('framereceived',frame=>{if(report.sample_frames.length<2)report.sample_frames.push(String(frame.payload).slice(0,1500))});
    });
    await page.goto('http://127.0.0.1:5173/#chart');
    await page.getByText('Chart ready',{exact:true}).waitFor({timeout:60000});
    const stored=async()=>{const r=await page.request.get('http://127.0.0.1:8000/api/v1/market/candles?limit=1&forming=true');if(!r.ok())throw new Error('Database read failed');return (await r.json()).data.at(-1)};
    report.first_database_observation=await stored();
    if(!report.first_database_observation)throw new Error('No PostgreSQL candles returned; start the API and collector first.');
    await page.evaluate(()=>{
      window.__liveProbe=[];
      const node=document.querySelector('[aria-label="Live stream status"]');
      const record=()=>{
        const eventAt=Number(node.dataset.eventTime),receivedAt=Number(node.dataset.receivedAt);
        if(eventAt&&receivedAt&&window.__liveProbe.at(-1)?.eventAt!==eventAt)
          window.__liveProbe.push({eventAt,receivedAt,paintedAt:Date.now(),price:document.querySelector('[aria-label="Last live price"]').textContent});
      };
      new MutationObserver(record).observe(node,{attributes:true,attributeFilter:['data-event-time']});record();
    });
    try{
      await page.waitForFunction(()=>window.__liveProbe.length>=8,{timeout:20000});
      report.stream_verified=true;
    }catch{report.stream_note='No eight valid messages within 20 seconds on this browser/network; controlled stream tests are recorded separately.'}
    report.stream_status=await page.getByLabel('Live stream status').textContent();
    report.messages=await page.evaluate(()=>window.__liveProbe);
    if(report.messages.length>1){
      const gaps=report.messages.slice(1).map((m,i)=>m.receivedAt-report.messages[i].receivedAt).sort((a,b)=>a-b);
      report.median_observed_message_interval_ms=gaps[Math.floor(gaps.length/2)];
      report.maximum_observed_DOM_update_ms=Math.max(...report.messages.map(m=>m.paintedAt-m.receivedAt));
    }
    for(let attempt=0;attempt<8;attempt++){
      report.last_database_observation=await stored();
      if(Date.parse(report.last_database_observation.received_at)>Date.parse(report.first_database_observation.received_at)){report.database_advanced=true;break}
      await new Promise(resolve=>setTimeout(resolve,2000));
    }
    await page.screenshot({path:'docs/screenshots/fast-live-btcusdt.png',fullPage:true});
    const storageResponse=await page.request.get('http://127.0.0.1:8000/api/v1/market/storage');
    const storage=(await storageResponse.json()).data;
    report.collector_poll_interval_seconds=storage.feeds.find(feed=>feed.id==='live:collector')?.state.poll_interval_seconds??null;
    report.status=report.stream_verified&&report.database_advanced?'actual_stream_and_periodic_postgres_verified':'partial_runtime_verification';
    fs.writeFileSync('docs/evidence/fast-live-runtime.json',JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify({...report,messages:report.messages.length,first_database_observation:report.first_database_observation?.received_at,last_database_observation:report.last_database_observation?.received_at},null,2));
    if(!report.stream_verified||!report.database_advanced)process.exitCode=2;
  }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
