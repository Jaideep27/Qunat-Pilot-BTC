// Record the real local release with captions. Requires API :8000 and dashboard :5173.
const {chromium}=require('../../frontend/node_modules/playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const crypto=require('node:crypto');
process.chdir(path.resolve(__dirname,'../..'));

(async()=>{
  const smoke=JSON.parse(fs.readFileSync('.verification/release-smoke.json','utf8'));
  assert.equal(smoke.status,'passed');
  assert.equal(smoke.mcp_tools_matched,9);
  const verification=JSON.parse(fs.readFileSync('docs/evidence/local-readiness-verification.json','utf8'));
  const recovery=JSON.parse(fs.readFileSync('docs/evidence/recovery-verification.json','utf8'));
  const inspection=spawnSync(process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python',
    ['-c','import json; from quantpilot.monitoring.diagnostics import doctor; print(json.dumps(doctor(),default=str))'],{encoding:'utf8',windowsHide:true});
  if(inspection.status!==0)throw new Error('Current doctor inspection failed; no video replaced.');
  const doctor=JSON.parse(inspection.stdout);
  async function read(resource){
    const response=await fetch('http://127.0.0.1:8000/api/v1/'+resource,{signal:AbortSignal.timeout(60000)});
    if(!response.ok)throw new Error(resource+' returned '+response.status);
    return response.json();
  }
  const [health,portfolio,prediction]=await Promise.all([read('health'),read('portfolio'),read('predictions')]);
  assert.equal(health.model_version,smoke.model_version);
  const forward=doctor.details.operations.forward_evidence,coverage=doctor.details.candle_coverage.markets.BTCUSDT;
  const runtime={captured_at:new Date().toISOString(),doctor,health,portfolio,prediction};
  const monitor=doctor.checks.running_monitor.status;
  fs.mkdirSync('docs/walkthrough',{recursive:true});
  const browser=await chromium.launch({channel:process.env.QUANTPILOT_BROWSER_CHANNEL||'chrome',headless:true});
  try {
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  const page=await context.newPage();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  // The provider socket denied in this agent session is not retried for recording.
  await page.routeWebSocket(/fstream\.binance\.com/,socket=>socket.close());
  await page.goto('http://127.0.0.1:5173/#chart');
  await page.getByText('Chart ready',{exact:true}).waitFor({timeout:60000});
  const scenes=[
    ['Chart workspace',0,20,`BTCUSDT PostgreSQL workspace: ${coverage.stored.toLocaleString('en-US')} of ${coverage.expected.toLocaleString('en-US')} five-minute candles in five years; ${coverage.missing} gaps. Seven timeframes, 86 studies and saved drawings. This recording disables the exchange socket and shows stored prices. Actual WebSocket latency is not demonstrated.`],
    ['Market',0,15,`Market context combines price, volume, positioning and daily sentiment with observation times. The independent collector reports a ${doctor.details.operations.collector.poll_interval_seconds}-second polling target. These are actual local database observations; this is a snapshot walkthrough, not continuous live trading.`],
    ['Prediction',0,20,`Frozen calibrated model ${health.model_version}. Snapshot decision: ${prediction.data.action}; reasons: ${(prediction.data.reasons??[]).join(', ')||'all gates passed'}. Predictions face cost, availability and risk gates. Explanations describe model associations; no thresholds were retuned for this recording.`],
    ['Research',0,20,'The research experiment uses 24 complete months, 209,983 common eligible observations and 106 features. The authorized policy quarantines 62 affected positioning rows and dependent windows without interpolation. Five-year price storage is separate from the 24-month ML experiment.'],
    ['Research',850,20,'Untouched August-September 2026 holdout: ROC-AUC 0.5388, Brier 0.2511 and balanced accuracy 50.65%. The frozen cost and risk policy produced zero holdout trades and zero return. These measured research results do not establish profitability.'],
    ['Paper portfolio',0,25,`Actual snapshot: ${forward.forward_decisions} forward decisions, ${forward.forward_fills} forward fills, ${forward.replayed_fills} replayed fills; equity ${Number(portfolio.data.equity).toLocaleString('en-US')} USDT. Monitor: ${monitor.replaceAll('_',' ')}. Verified: ${verification.python.passed} Python cases, ${verification.browser.passed} browser cases, nine SDK tool/API comparisons and a ${recovery.databases[0].tables}-table restore drill. Observation span is not uptime. Public deployment is deferred.`],
  ];
  const frames=[],captures=[];
  for(const [view,scroll,seconds,caption] of scenes){
    await page.getByRole('button',{name:view,exact:true}).click();
    await page.waitForFunction(()=>{
      const icon=document.querySelector('[aria-label="Refresh data"] svg');
      return icon&&!icon.classList.contains('spin');
    },undefined,{timeout:90000});
    if(view==='Chart workspace'){
      await page.getByText('Chart ready',{exact:true}).waitFor({timeout:60000});
      await page.getByText('postgresql',{exact:true}).waitFor({timeout:60000});
    }
    if(view==='Research')await page.getByRole('heading',{name:'Probability calibration',exact:true}).waitFor({timeout:60000});
    if(view==='Paper portfolio'){
      await page.getByRole('heading',{name:'Worker monitoring',exact:true}).waitFor();
      await page.waitForFunction(()=>!document.querySelector('[aria-label="Paper worker monitoring"]')?.textContent.includes('Loading recorded worker runs'));
    }
    await page.evaluate(({scroll,caption})=>{
      window.scrollTo(0,scroll);
      let panel=document.getElementById('walkthrough-caption');
      if(!panel){
        panel=document.createElement('div');panel.id='walkthrough-caption';
        Object.assign(panel.style,{position:'fixed',bottom:'18px',left:'220px',right:'28px',zIndex:'9999',
          padding:'18px 22px',background:'rgba(8,16,23,.97)',border:'1px solid #3f9d83',
          color:'#edf7f5',font:'18px/1.5 system-ui',borderRadius:'10px',boxShadow:'0 8px 30px #0009'});
        document.body.appendChild(panel);
      }
      panel.textContent=caption;
    },{scroll,caption});
    await page.waitForTimeout(500);
    const file='scene-'+String(frames.length+1).padStart(2,'0')+'.png';
    const screenshot=await page.screenshot({path:path.join('docs/walkthrough',file)});
    captures.push({file,sha256:crypto.createHash('sha256').update(screenshot).digest('hex'),captured_at:new Date().toISOString()});
    frames.push({seconds,image:(await page.screenshot({type:'jpeg',quality:90})).toString('base64')});
    console.log('Captured '+view+' ('+seconds+' seconds)');
  }
  assert.deepEqual(errors,[]);
  // Chrome's built-in recorder encodes a captioned screenshot walkthrough.
  // No downloaded browser encoder, fabricated UI, or provider requests are used.
  const recording=await context.newPage();
  await recording.setContent('<canvas width="1440" height="1000"></canvas>');
  console.log('Encoding 120-second captioned walkthrough with browser MediaRecorder');
  const result=await recording.evaluate(async frames=>{
    const canvas=document.querySelector('canvas'), ctx=canvas.getContext('2d');
    const images=await Promise.all(frames.map(frame=>new Promise(resolve=>{
      const img=new Image();img.onload=()=>resolve(img);img.src='data:image/jpeg;base64,'+frame.image;
    })));
    const stream=canvas.captureStream(2),chunks=[];
    const recorder=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8',videoBitsPerSecond:1500000});
    recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};
    const finished=new Promise(resolve=>recorder.onstop=resolve);
    let scene=0,elapsed=0;
    ctx.drawImage(images[0],0,0);recorder.start(1000);
    const redraw=setInterval(()=>ctx.drawImage(images[scene],0,0),500);
    for(const frame of frames){
      await new Promise(resolve=>setTimeout(resolve,frame.seconds*1000));
      elapsed+=frame.seconds;scene=Math.min(scene+1,images.length-1);
    }
    clearInterval(redraw);recorder.stop();await finished;
    stream.getTracks().forEach(track=>track.stop());
    const bytes=new Uint8Array(await new Blob(chunks,{type:'video/webm'}).arrayBuffer());
    let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
    return {seconds:elapsed,base64:btoa(binary)};
  },frames);
  const target=path.resolve('docs/walkthrough/quantpilot-btc.webm');
  const video=Buffer.from(result.base64,'base64');
  fs.writeFileSync(target+'.partial',video);fs.renameSync(target+'.partial',target);
  fs.writeFileSync('docs/walkthrough/runtime-snapshot.json',JSON.stringify(runtime,null,2)+'\n');
  fs.writeFileSync('docs/walkthrough/recording.json',JSON.stringify({format:'video/webm;codecs=vp8',
    seconds:result.seconds,width:1440,height:1000,scenes:scenes.map(([view,scroll,seconds,caption])=>({view,scroll,seconds,caption})),
    recorded_at:new Date().toISOString(),captures,page_errors:errors,
    provider_socket:'Intentionally disabled for recording; actual WebSocket latency remains unverified',
    service_readiness:doctor.ready,running_monitor:monitor,runtime_evidence:'runtime-snapshot.json',
    video_sha256:crypto.createHash('sha256').update(video).digest('hex'),
    style:'Captioned snapshots of actual local UI/database; no voiceover or continuous stream demonstration',
    model_version:smoke.model_version},null,2));
  console.log('Saved captioned screenshot walkthrough: '+target);
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1});
