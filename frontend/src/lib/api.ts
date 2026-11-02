export interface Envelope<T> {
  data:T;
  provenance:{source:string; observed_at:string|null; served_at:string; freshness:'fresh'|'stale'|'missing'; age_seconds:number|null};
  config_version:string; model_version:string|null;
}
export interface Candle {event_time:string; available_at:string; open:number; high:number; low:number; close:number; volume:number; ema21?:number; received_at?:string; complete?:boolean; source?:string}
export interface Trading {net_return:number;max_drawdown:number;profit_factor:number|null;turnover:number;exposure:number;trades:number;curve:{time:string;equity:number}[];fills:unknown[]}
export interface Comparison {group:string;kind:string;threshold:number;holdout:{brier:number;balanced_accuracy:number;roc_auc:number|null;calibration:{predicted:number;observed:number}[]};trading:Trading}
export interface Research {status:string;comparisons:Comparison[];benchmarks:Record<string,Partial<Trading>>;reason?:string;metadata?:{eligible_rows:number;version:string}}
export interface Coverage {release_ready:boolean;strict_release_ready?:boolean;quarantine?:{source_rows:number;source_fraction:number;feature_lookback_bars:number;status:string};status?:string;start?:string;end_exclusive?:string;reason?:string;families?:Record<string,{rows:number;missing_windows:number;native_missing_count?:number;invalid_rows?:number;issues:string[]}>}
export const preview = new URLSearchParams(location.search).has('preview');
// The public gateway marks its page; ?public shows the same read-only view locally.
export const publicView = document.querySelector('meta[name="quantpilot-public"]')!==null||new URLSearchParams(location.search).has('public');
export async function request<T>(resource:string):Promise<Envelope<T>> {
  if(preview) return (await import('./preview')).fixture(resource) as Envelope<T>;
  const controller = new AbortController();
  const timer = setTimeout(()=>controller.abort(),30000);
  try {
    const response = await fetch((import.meta.env.VITE_API_URL ?? '')+'/api/v1/'+resource,{signal:controller.signal});
    if(!response.ok) throw new Error('Service returned '+response.status);
    return await response.json();
  } finally { clearTimeout(timer); }
}

export async function post<T>(resource:string,body:unknown):Promise<Envelope<T>>{
  if(publicView) throw new Error('This public view is read-only.');
  const response=await fetch((import.meta.env.VITE_API_URL??'')+'/api/v1/'+resource,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok){const detail=await response.json().catch(()=>({}));throw new Error(detail.detail??('Service returned '+response.status))}
  return await response.json();
}
