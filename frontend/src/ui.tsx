import {Component,useEffect,useId,useState,type ReactNode} from 'react';
import {ChevronDown,Database,Info,RefreshCw} from 'lucide-react';
import {request,type Envelope} from './api';
import {glossary} from './glossary';

export const money=(n:number|null|undefined,digits=2)=>n==null?'—':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:digits,minimumFractionDigits:digits}).format(n);
export const num=(n:number|null|undefined,d=2)=>n==null||!Number.isFinite(n)?'—':n.toLocaleString('en-US',{maximumFractionDigits:d});
export const pct=(n:number|null|undefined,d=1)=>n==null||!Number.isFinite(n)?'—':(n*100).toFixed(d)+'%';
export const signedPct=(n:number|null|undefined,d=2)=>n==null||!Number.isFinite(n)?'—':(n>0?'+':'')+(n*100).toFixed(d)+'%';
export const stamp=(s:string|null|undefined)=>s?new Date(s).toLocaleString(undefined,{timeZone:'UTC',dateStyle:'medium',timeStyle:'short'})+' UTC':'Awaiting observation';
export function ago(value:string|number|null|undefined,now=Date.now()){
  if(value==null)return '—';
  const seconds=Math.max(0,Math.round((now-(typeof value==='number'?value:Date.parse(value)))/1000));
  if(seconds<60)return seconds+'s ago';
  if(seconds<3600)return Math.round(seconds/60)+'m ago';
  if(seconds<172800)return Math.round(seconds/3600)+'h ago';
  return Math.round(seconds/86400)+'d ago';
}
export function until(value:string|null|undefined,now=Date.now()){
  if(!value)return '—';
  const minutes=Math.round((Date.parse(value)-now)/60000);
  if(minutes<=0)return 'now';
  if(minutes<60)return `in ${minutes}m`;
  if(minutes<2880)return `in ${Math.floor(minutes/60)}h ${minutes%60}m`;
  return `in ${Math.round(minutes/1440)} days`;
}

// One in-flight request per resource: the top bar and the page share the same download.
const cache=new Map<string,{at:number;promise:Promise<Envelope<unknown>>}>();
function cached<T>(resource:string,maxAge:number):Promise<Envelope<T>>{
  const hit=cache.get(resource);
  if(hit&&Date.now()-hit.at<maxAge)return hit.promise as Promise<Envelope<T>>;
  const promise=request<T>(resource);
  cache.set(resource,{at:Date.now(),promise});
  promise.catch(()=>cache.delete(resource));
  return promise;
}

export type Resource<T>={data:T|undefined;envelope:Envelope<T>|undefined;error:boolean;loading:boolean;updatedAt:number|null;reload:()=>void};
export function useResource<T>(resource:string|null,every=60000):Resource<T>{
  const [state,setState]=useState<{envelope?:Envelope<T>;error:boolean;loading:boolean;updatedAt:number|null}>({error:false,loading:!!resource,updatedAt:null});
  const [revision,setRevision]=useState(0);
  useEffect(()=>{
    if(!resource)return;
    let alive=true;
    const load=(force:boolean)=>{
      if(force)cache.delete(resource);
      cached<T>(resource,Math.min(every,20000)).then(envelope=>{if(alive)setState({envelope,error:false,loading:false,updatedAt:Date.now()})})
        .catch(()=>{if(alive)setState(s=>({...s,error:true,loading:false}))});
    };
    load(revision>0);
    const timer=setInterval(()=>load(true),every);
    return()=>{alive=false;clearInterval(timer)};
  },[resource,every,revision]);
  return {data:state.envelope?.data,envelope:state.envelope,error:state.error,loading:state.loading,updatedAt:state.updatedAt,
    reload:()=>setRevision(r=>r+1)};
}

export function useNow(every=1000){
  const [now,setNow]=useState(Date.now());
  useEffect(()=>{const t=setInterval(()=>setNow(Date.now()),every);return()=>clearInterval(t)},[every]);
  return now;
}

export function Term({k,children}:{k:string;children:ReactNode}){
  const id=useId();
  const text=glossary[k];
  if(!text)return <>{children}</>;
  return <span className="term"><button type="button" className="term-trigger" aria-describedby={id}>{children}<Info size={11} aria-hidden/></button>
    <span role="tooltip" id={id} className="term-tip">{text}</span></span>;
}

export function Card({title,label,tag,actions,children,className='',span}:{title?:ReactNode;label?:string;tag?:ReactNode;actions?:ReactNode;
  children:ReactNode;className?:string;span?:1|2|3}){
  return <section className={`card ${span?'span-'+span:''} ${className}`} aria-label={label??(typeof title==='string'?title:undefined)}>
    {(title||tag||actions)&&<header className="card-head">{title&&<h2>{title}</h2>}<div className="card-head-side">{tag&&<span className="tag">{tag}</span>}{actions}</div></header>}
    <div className="card-body">{children}</div>
  </section>;
}

export function Collapsible({title,summary,children,defaultOpen=false,label}:{title:string;summary?:ReactNode;children:ReactNode;defaultOpen?:boolean;label?:string}){
  return <details className="collapsible" open={defaultOpen} aria-label={label??title}>
    <summary><span className="collapsible-title">{title}</span>{summary&&<span className="collapsible-summary">{summary}</span>}<ChevronDown size={16} className="chev" aria-hidden/></summary>
    <div className="collapsible-body">{children}</div>
  </details>;
}

export function Segmented<T extends string>({options,value,onChange,label}:{options:readonly T[];value:T;onChange:(v:T)=>void;label:string}){
  return <div className="segmented" role="group" aria-label={label}>{options.map(o=>
    <button key={o} type="button" aria-pressed={o===value} className={o===value?'on':''} onClick={()=>onChange(o)}>{o}</button>)}</div>;
}

export function Stat({label,value,detail,tone,term}:{label:string;value:ReactNode;detail?:ReactNode;tone?:'up'|'down'|'warn'|'muted';term?:string}){
  return <div className="stat"><span className="stat-label">{term?<Term k={term}>{label}</Term>:label}</span>
    <strong className={tone?'tone-'+tone:''}>{value}</strong>{detail&&<small>{detail}</small>}</div>;
}

export function Pill({tone='muted',children}:{tone?:'ok'|'warn'|'bad'|'muted'|'accent';children:ReactNode}){
  return <span className={'pill pill-'+tone}>{children}</span>;
}

export function Empty({title,children}:{title:string;children?:ReactNode}){
  return <div className="empty"><Database size={22} aria-hidden/><h3>{title}</h3>{children&&<p>{children}</p>}</div>;
}

export function Loading({lines=3}:{lines?:number}){
  return <div className="skeleton" aria-busy="true" aria-label="Loading">{Array.from({length:lines},(_,i)=><span key={i} style={{width:`${90-i*15}%`}}/>)}</div>;
}

export function Updated({resource,label='Updated'}:{resource:Resource<unknown>;label?:string}){
  const now=useNow(5000);
  return <button type="button" className="updated" onClick={resource.reload} title="Refresh now">
    <RefreshCw size={12} className={resource.loading?'spin':''} aria-hidden/>{resource.error?'Unavailable · retry':resource.updatedAt?`${label} ${ago(resource.updatedAt,now)}`:'Loading…'}</button>;
}

export function Meter({value,min=0,max=100,tone}:{value:number|null|undefined;min?:number;max?:number;tone?:'up'|'down'|'warn'}){
  const ratio=value==null?0:Math.max(0,Math.min(1,(value-min)/(max-min)));
  return <div className="meter" aria-hidden><i className={tone?'tone-bg-'+tone:''} style={{width:ratio*100+'%'}}/></div>;
}

export function PageHead({title,description,children}:{title:string;description:string;children?:ReactNode}){
  return <div className="page-head"><div><h1>{title}</h1><p>{description}</p></div>{children&&<div className="page-head-side">{children}</div>}</div>;
}

// One failing page or panel must not blank the whole app.
export class Boundary extends Component<{children:ReactNode;name?:string},{failed:boolean}>{
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true}}
  render(){return this.state.failed?<div className="card card-body" role="status"><p className="note">{this.props.name??'This section'} could not be displayed with the data received.{' '}
    <button type="button" className="link" onClick={()=>this.setState({failed:false})}>Try again</button></p></div>:this.props.children}
}
