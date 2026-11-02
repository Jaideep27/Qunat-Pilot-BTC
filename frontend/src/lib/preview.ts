// Explicit UI fixture. Never sent to the backend, audit, model or accounting pipeline.
import type {Envelope, Candle} from './api';
const end=Date.UTC(2026,8,30,18);
const candles:Candle[]=Array.from({length:160},(_,i)=>{
  const open=64200+i*4+Math.sin(i*.18)*190,close=open+Math.sin(i*.7)*38;
  return {event_time:new Date(end-(160-i)*300000).toISOString(),available_at:new Date(end-(159-i)*300000).toISOString(),
    open,close,high:Math.max(open,close)+22,low:Math.min(open,close)-18,volume:130+Math.abs(Math.sin(i))*300};
});
export function fixture(resource:string):Envelope<unknown>{
  const values:Record<string,unknown>={
    candles,
    positioning:[{open_interest:82314,global_ratio:1.42,top_account_ratio:1.68,top_position_ratio:1.21}],
    sentiment:[{fear_greed:54,event_time:'2026-09-29T00:00:00Z'}],
    coverage:{release_ready:false,status:'fixture',reason:'Interface preview only. Historical coverage has not been verified.'},
    backtests:{status:'blocked',comparisons:[],benchmarks:{},reason:'No measured evaluation is available. Run the coverage audit before training.'},
    health:{status:'preview'}
  };
  return {data:values[resource.split('?')[0]],provenance:{source:'Deterministic interface fixture',observed_at:new Date(end).toISOString(),
    served_at:new Date().toISOString(),freshness:'stale',age_seconds:86400},config_version:'analysis-v1',model_version:null};
}

