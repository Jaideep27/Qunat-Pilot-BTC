import {useState,type ReactNode} from 'react';
import {Sparkles} from 'lucide-react';
import {post,publicView,request} from '../lib/api';
import {ago} from './ui';

export type Tone='up'|'down'|'hot'|'cold'|'neutral'|'quiet'|'busy'|'warn';
export type Zone=[number|null,string,Tone,string];
export type Indicator={id:string;name:string;group:string;scope:'timeframe'|'market';unit:string;scale:[number,number];
  settings:string;what:string;how:string;zones:Zone[];study:string[]|null};
export type Catalogue={groups:string[];timeframes:string[];indicators:Indicator[]};
export type Reading={id:string;value:number|null;zone:number|null;label:string|null;tone:Tone|null;sentence:string|null;
  spark:(number|null)[];detail?:{atr:number;atr_pct:number}|null};
export type Check={label:string;ok:boolean|null;value:string};
export type History={verdict:string;passed:boolean;trades:number;win_rate:number|null;break_even:number|null;account_return:number|null;
  max_drawdown:number|null;buy_and_hold:number|null;profit_factor:number|null;start:string|null;end:string|null;curve:{date:string;equity:number}[]};
export type StyleView={id:string;kind:string;name:string;what:string;status:string;tone:Tone;says:string;checklist:Check[];history:History|null;
  levels?:{turns_up_above:number;turns_down_below:number;stop_distance:number|null}};
export type Version={id:string;side:'long'|'short';status:string;tone:Tone;shadow:boolean;trigger:{wakes:string;seen:string|null};
  checklist:Check[];passed:number;total:number;stop_target:string;history:{setups:number;win_rate:number|null;mean_r:number|null;went_their_way_4h:number|null}|null};
export type Playbook={id:string;name:string;family:string;what:string;versions:Version[]};
export type Level={id:string;label:string;kind:'support'|'resistance'|'reference';price:number;distance:number};
export type Group={up:number;down:number;total:number;lean:'up'|'down'|'mixed';line:string};
export type Snapshot={built_at:string;data_time:string;symbol:string;note:string;
  price:{last:number|null;change_24h:number|null;high_24h:number|null;low_24h:number|null;volume_24h:number|null};
  regime:{id:string|null;text:string};
  timeframes:Record<string,{time:string|null;close:number|null;items:Reading[]}>;
  market:{time:string|null;items:Reading[]};summary:Record<string,Record<string,Group>>;levels:Level[];
  range_4h:{band:{low:number|null;high:number|null;position:number|null};forecast:null|{low:number|null;high:number|null;probability_up:number|null;status:string}};
  strategies:{styles:StyleView[];rulebook:{time:string|null;regime:string;verdict:string;playbooks:Playbook[];firing:{id:string;side:string;name:string}[]}};
  calendar:{upcoming:{label:string;kind:string;at:string}[];recent:{label:string;kind:string;at:string}[]};
  headlines:{items:{id:string;title:string;url:string;source:string;available_at:string;excerpt:string}[];
    analyses:{summary:string;sentiment:string;relevance?:number;source_ids:string[]}[]}};
/** Only a real snapshot counts: an unexpected payload must never blank a page. */
export const isSnapshot=(x:unknown):x is Snapshot=>!!x&&typeof x==='object'&&'built_at' in x&&'timeframes' in x&&'strategies' in x;
export const isCatalogue=(x:unknown):x is Catalogue=>!!x&&typeof x==='object'&&Array.isArray((x as Catalogue).indicators)&&Array.isArray((x as Catalogue).groups);

export type Explanation={title:string;explanation:string;what_to_watch:string[];model:string;created_at:string;cached?:boolean};

export const TONE_CLASS:Record<string,string>={up:'t-up',hot:'t-hot',down:'t-down',cold:'t-cold',neutral:'t-neutral',quiet:'t-quiet',busy:'t-busy',warn:'t-warn'};
export const STATUS_TONE:Record<string,Tone>={bullish:'up',bearish:'down',collecting:'up',firing:'up',watching:'neutral',
  'standing aside':'neutral',off:'quiet'};

/** A value in the indicator's own unit. */
export function fmt(value:number|null|undefined,unit:string){
  if(value==null||!Number.isFinite(value))return '—';
  if(unit==='%')return (value*100).toFixed(Math.abs(value)<.01?2:1)+'%';
  if(unit==='%yr')return (value>0?'+':'')+(value*100).toFixed(1)+'% / yr';
  if(unit==='pct')return Math.round(value)+'th pct';
  if(unit==='x')return value.toFixed(2)+'×';
  if(unit==='ATR')return (value>0?'+':'')+value.toFixed(2)+' ATR';
  if(unit==='SD')return (value>0?'+':'')+value.toFixed(2)+' SD';
  if(unit==='state')return value>.5?'on / up':value<-.5?'down':'mixed / off';
  return Math.abs(value)>=100?value.toFixed(0):value.toFixed(Math.abs(value)<1?2:1);
}

/** The indicator's zones as coloured segments across its scale, with a marker at the current value. */
export function ZoneBar({ind,value}:{ind:Indicator;value:number|null}){
  const [lo,hi]=ind.scale,span=hi-lo||1;
  const at=(v:number)=>Math.min(100,Math.max(0,((v-lo)/span)*100));
  let prev=lo;
  const parts=ind.zones.map(([upper,label,tone])=>{const from=at(prev),to=upper==null?100:at(upper);prev=upper??hi;
    return {label,tone,width:Math.max(0,to-from)}}).filter(p=>p.width>0);
  return <div className="zonebar" role="img" aria-label={`${ind.name}: ${value==null?'no reading':fmt(value,ind.unit)}`}>
    <div className="zonebar-track">{parts.map((p,i)=><span key={i} className={'zone '+TONE_CLASS[p.tone]} style={{width:p.width+'%'}} title={p.label}/>)}</div>
    {value!=null&&Number.isFinite(value)&&<i className="zonebar-mark" style={{left:at(value)+'%'}}/>}
  </div>;
}

export function Spark({values,label}:{values:(number|null)[];label:string}){
  const v=values.filter((x):x is number=>x!=null&&Number.isFinite(x));
  if(v.length<2)return <svg className="mini-spark" aria-hidden viewBox="0 0 100 28"/>;
  const lo=Math.min(...v),hi=Math.max(...v),span=hi-lo||1;
  const points=v.map((x,i)=>`${(i/(v.length-1))*100},${26-((x-lo)/span)*24}`).join(' ');
  return <svg className="mini-spark" role="img" aria-label={label} viewBox="0 0 100 28" preserveAspectRatio="none">
    <polyline points={points} fill="none" strokeWidth="1.5" vectorEffect="non-scaling-stroke"/></svg>;
}

export function Badge({tone,children}:{tone:Tone|null|undefined;children:ReactNode}){
  return <span className={'badge '+TONE_CLASS[tone??'neutral']}>{children}</span>;
}

export function AIText({model,at,label='AI analysis'}:{model:string;at:string;label?:string}){
  const name=model.includes('haiku')?'Claude Haiku':model.includes('sonnet')?'Claude Sonnet':'Claude';
  return <small className="ai-label"><Sparkles size={12} aria-hidden/>{label} by {name} · {ago(at)} · not financial advice</small>;
}

/** Add studies to the chart's saved selection (max 12), then open the chart. */
export function showOnChart(studies:string[]){
  try{
    const current:string[]=JSON.parse(localStorage.getItem('qp:studies')??'[]')??[];
    const next=[...current.filter(s=>!studies.includes(s)),...studies].slice(-12);
    localStorage.setItem('qp:studies',JSON.stringify(next));
  }catch{/* storage may be unavailable; the chart opens with its defaults */}
  location.hash='#chart';
}

/** "Explain with Claude": a fresh (cached per snapshot) explanation locally; the last stored one on the public link. */
export function ExplainBox({kind,refId,timeframe,studies,label='Explain with Claude'}:{kind:'indicator'|'strategy'|'news'|'chart';
  refId:string;timeframe?:string;studies?:string[];label?:string}){
  const [state,setState]=useState<{busy:boolean;error?:string;result?:Explanation|null}>({busy:false});
  const run=async()=>{
    setState({busy:true});
    try{
      if(publicView){
        const q=new URLSearchParams({kind,ref:refId,...(timeframe?{timeframe}:{})});
        const env=await request<Explanation|null>('analysis/explanations?'+q.toString());
        setState({busy:false,result:env.data});
      }else{
        const env=await post<Explanation>('analysis/explain',{kind,ref:refId,timeframe:timeframe??null,studies:studies??[]});
        setState({busy:false,result:env.data});
      }
    }catch(e){setState({busy:false,error:e instanceof Error?e.message:'Claude could not answer right now.'})}
  };
  const r=state.result;
  return <div className="explain">
    {!r&&<button type="button" className="explain-button" onClick={()=>void run()} disabled={state.busy}>
      <Sparkles size={14} aria-hidden/>{state.busy?'Claude is writing…':publicView?'Show Claude’s last explanation':label}</button>}
    {state.error&&<p role="alert" className="explain-error">{state.error}</p>}
    {r===null&&<p className="muted">Claude has not explained this yet.</p>}
    {r&&<div className="explain-result" role="region" aria-label="Claude explanation">
      <strong>{r.title}</strong><p>{r.explanation}</p>
      {r.what_to_watch?.length>0&&<ul>{r.what_to_watch.map((w,i)=><li key={i}>{w}</li>)}</ul>}
      <AIText model={r.model} at={r.created_at} label="Explained"/>
    </div>}
  </div>;
}
