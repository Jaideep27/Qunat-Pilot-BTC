// Dependency-free manifest enumeration and bounded public-feed connectivity probe.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const anchor = new Date(process.argv[2] || '2026-10-18T00:00:00Z');
const end = new Date(Date.UTC(anchor.getUTCFullYear(),anchor.getUTCMonth(),1));
const start = new Date(Date.UTC(end.getUTCFullYear()-2,end.getUTCMonth(),1));
const warmup = new Date(+start-40*86400000);
const base='https://data.binance.vision/data/futures/um';
const manifest=[];
const month = new Date(Date.UTC(warmup.getUTCFullYear(),warmup.getUTCMonth(),1));
for(;month<end;month.setUTCMonth(month.getUTCMonth()+1)){
  const stamp=month.toISOString().slice(0,7);
  for(const [family,directory] of [['candles','klines'],['mark','markPriceKlines'],['index','indexPriceKlines']]){
    const filename='BTCUSDT-5m-'+stamp+'.zip';
    manifest.push({family,filename:family+'-'+filename,url:base+'/monthly/'+directory+'/BTCUSDT/5m/'+filename});
  }
  const filename='BTCUSDT-fundingRate-'+stamp+'.zip';
  manifest.push({family:'funding',filename,url:base+'/monthly/fundingRate/BTCUSDT/'+filename});
}
for(let t=+warmup;t<+end;t+=86400000){
  const filename='BTCUSDT-metrics-'+new Date(t).toISOString().slice(0,10)+'.zip';
  manifest.push({family:'positioning',filename,url:base+'/daily/metrics/BTCUSDT/'+filename});
}
assert.equal(new Set(manifest.map(x=>x.filename)).size,manifest.length);
fs.mkdirSync('docs',{recursive:true});
fs.writeFileSync('docs/expected-archives.json',JSON.stringify({start:start.toISOString(),end_exclusive:end.toISOString(),warmup_start:warmup.toISOString(),archives:manifest},null,2));
async function probe(url){
  try{
    const r=await fetch(url,{signal:AbortSignal.timeout(10000)});
    return {url,status:r.status,ok:r.ok};
  }catch(e){return {url,ok:false,error:String(e.cause?.code || e.message)};}
}
(async()=>{
  const samples=['candles','mark','index','funding','positioning'].map(f=>manifest.find(x=>x.family===f).url+'.CHECKSUM');
  samples.push('https://api.alternative.me/fng/?limit=1');
  const probes=await Promise.all(samples.map(probe));
  const counts={};
  for(const item of manifest)counts[item.family]=(counts[item.family]||0)+1;
  const result={status:'release_blocked',release_ready:false,anchor:anchor.toISOString(),start:start.toISOString(),end_exclusive:end.toISOString(),
    warmup_start:warmup.toISOString(),expected_archive_counts:counts,expected_archives:manifest.length,
    expected_5m_windows:(+end-+warmup)/300000,verified_archives:0,probes,
    reason:'Connectivity preflight only. No historical observations or checksums have been verified. Run the Python audit in a network-enabled environment.'};
  fs.writeFileSync('docs/coverage-report.json',JSON.stringify(result,null,2));
  console.log(JSON.stringify(result,null,2));
})().catch(e=>{console.error(e);process.exitCode=1});

