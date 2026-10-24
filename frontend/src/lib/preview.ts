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
    indicators:{rsi_14:58.3,adx:27.4,atr:186.2,relative_volume:1.24,ema_21_distance:.62,ema_50_distance:1.28,vwap_distance:.35,
      prior_day_high:1.2,prior_day_low:3.4,swing_high_distance:.9,swing_low_distance:2.1,mark_index_premium:.0001},
    positioning:[{open_interest:82314,global_ratio:1.42,top_account_ratio:1.68,top_position_ratio:1.21}],
    sentiment:[{fear_greed:54,event_time:'2026-09-29T00:00:00Z'}],
    predictions:{action:'NO_TRADE',reasons:['MODEL_RELEASE_BLOCKED'],probability:null},
    coverage:{release_ready:false,status:'fixture',reason:'Interface preview only. Historical coverage has not been verified.'},
    backtests:{status:'blocked',comparisons:[],benchmarks:{},reason:'No measured evaluation is available. Run the coverage audit before training.'},
    portfolio:{cash:10000,equity:10000,position:null,fees:0,funding_total:0,realized_pnl:0,paused:true,halted:false,
      last_worker:null,fills:[],decisions:[],curve:[]},
    health:{status:'preview',model_released:false}
  };
  return {data:values[resource.split('?')[0]],provenance:{source:'Deterministic interface fixture',observed_at:new Date(end).toISOString(),
    served_at:new Date().toISOString(),freshness:'stale',age_seconds:86400},config_version:'risk-v1',model_version:null};
}

