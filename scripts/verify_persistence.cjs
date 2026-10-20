// Observe actual PostgreSQL receipt times through the local API. No provider requests.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
process.chdir(path.resolve(__dirname,'..'));
const median=values=>{const sorted=[...values].sort((a,b)=>a-b),middle=Math.floor(sorted.length/2);return sorted.length%2?sorted[middle]:(sorted[middle-1]+sorted[middle])/2};
(async()=>{
  const report={checked_at:new Date().toISOString(),market:'BTCUSDT',
    source:'Actual local API reads of PostgreSQL forming/completed candle snapshots',
    scope:'Read-only persistence observation; no exchange requests, worker execution or fixture writes',observations:[]};
  for(let attempt=0;attempt<20;attempt++){
    const started=Date.now();
    const response=await fetch('http://127.0.0.1:8000/api/v1/market/candles?limit=1&forming=true',{signal:AbortSignal.timeout(10000)});
    assert.equal(response.status,200,'Persistent candle API must return 200');
    const row=(await response.json()).data.at(-1);
    assert.ok(row&&['open','high','low','close'].every(key=>Number.isFinite(row[key])),'Actual candle must contain finite OHLC');
    assert.ok(row.low<=Math.min(row.open,row.close)&&row.high>=Math.max(row.open,row.close),'Invalid candle range');
    assert.ok(Number.isFinite(Date.parse(row.received_at)),'Receipt timestamp is required');
    report.observations.push({observed_at:new Date().toISOString(),received_at:row.received_at,
      event_time:row.event_time,close:row.close,source:row.source,request_ms:Date.now()-started});
    if(new Set(report.observations.map(row=>Date.parse(row.received_at))).size>=4)break;
    if(attempt<19)await new Promise(resolve=>setTimeout(resolve,1000));
  }
  const writes=[...new Set(report.observations.map(row=>Date.parse(row.received_at)))];
  const intervals=writes.slice(1).map((stamp,index)=>stamp-writes[index]);
  assert.ok(intervals.every(value=>value>0),'Receipt timestamps must advance chronologically');
  report.distinct_saved_observations=writes.length;
  report.saved_snapshot_intervals_ms=intervals;
  report.median_saved_snapshot_interval_ms=intervals.length?median(intervals):null;
  report.median_local_api_request_ms=median(report.observations.map(row=>row.request_ms));
  report.price_changed=new Set(report.observations.map(row=>row.close)).size>1;
  report.status=writes.length>=2?'periodic_postgresql_updates_verified':'no_database_advance_observed';
  report.note='Intervals describe observed receipt timestamps, not exchange-to-database or exchange-to-browser latency. A small local sample does not establish uptime.';
  fs.writeFileSync('docs/evidence/persistence-runtime.json',JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({...report,observations:report.observations.length},null,2));
  if(writes.length<2)process.exitCode=2;
})().catch(error=>{console.error(error.message);process.exitCode=1});
