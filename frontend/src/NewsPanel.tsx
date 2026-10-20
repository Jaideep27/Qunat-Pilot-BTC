import React,{useEffect,useState} from 'react';
import {request,type Envelope} from './api';

type News={items:{id:string;title:string;url:string;source:string;available_at:string;published_at:string|null;excerpt:string}[];
  feeds:{source:string;status:string;checked_at:string}[];
  runs:{id:string;status:string;provider?:string;model?:string;available_at?:string;result?:{events:{summary:string;sentiment:string;novelty?:string;entities?:string[];
    uncertainties:string[];source_ids:string[]}[]}}[];
  analysis_status?:string;providers?:Record<string,{model:string;analysis_status:string}>;total_items:number;scope:string;model:string;forecasting_status:string};
const delays:Record<string,string>={
  delayed_usage_limit:'Analysis delayed: the reasoner\'s subscription usage limit was reached. Collection continues and analysis retries automatically.',
  delayed_failure:'Analysis delayed: the latest analysis attempt failed. It retries automatically.'};
const states:Record<string,string>={current:'current',running:'running',not_started:'not started',
  delayed_usage_limit:'delayed: usage limit',delayed_failure:'delayed: failed attempt'};
const novelty:Record<string,string>={new:'new information',follow_up:'follow-up',duplicate:'duplicate report',correction:'correction'};

export default function NewsPanel(){
  const [value,setValue]=useState<Envelope<News>|null>(null);
  const [error,setError]=useState('');
  useEffect(()=>{let alive=true;const load=()=>request('news').then(r=>{if(alive){setValue(r as Envelope<News>);setError('');}})
    .catch(()=>{if(alive)setError('News service unavailable. Market forecasts may not include current news.');});
    load();const timer=setInterval(load,60000);return()=>{alive=false;clearInterval(timer);};},[]);
  const d=value?.data;
  return <section className="panel" aria-label="News evidence">
    <div className="panel-heading"><h2>News & macro evidence</h2><span className="subtle-tag">SHADOW RESEARCH</span></div>
    {error&&<p role="status">{error}</p>}
    {!d&&!error&&<p>Loading news evidence...</p>}
    {d&&<><p className="panel-source">{d.scope} News reasoning does not change the released trading policy.</p>
      <div className="indicator-list">{d.feeds.map(f=><div key={f.source}><span>{f.source}</span><strong>{f.status} · {new Date(f.checked_at).toLocaleString()}</strong></div>)}</div>
      {!d.items.length&&<p>No news collected yet. Sentiment is unavailable until verified observations arrive.</p>}
      {d.items.slice(0,12).map(i=><article key={i.id} className='news-item'>
        <a href={/^https?:\/\//.test(i.url)?i.url:undefined} target="_blank" rel="noreferrer">{i.title}</a>
        <p>{i.excerpt}</p><small>{i.source} · Collected {new Date(i.available_at).toLocaleString()} · Publication {i.published_at?new Date(i.published_at).toLocaleString():'unknown'}</small>
      </article>)}
      <h3>Agent analysis</h3>
      {d.providers&&<ul aria-label="News reasoners" className="operations-checks">{Object.entries(d.providers).map(([name,p])=>
        <li key={name}>{name} · {p.model} · {states[p.analysis_status]??p.analysis_status}</li>)}</ul>}
      {/* With several reasoners, one current reasoner means analysis is not delayed overall. */}
      {d.analysis_status&&delays[d.analysis_status]&&!Object.values(d.providers??{}).some(p=>p.analysis_status==='current')
        &&<p role="status">{delays[d.analysis_status]}</p>}
      {!d.runs.some(r=>r.status==='completed')&&<p>No completed analysis. Pending news is not classified as neutral.</p>}
      {d.runs.filter(r=>r.status==='completed').slice(0,3).flatMap(r=>(r.result?.events??[]).map((e,n)=><article key={r.id+n}>
        <strong>{e.sentiment} textual sentiment{e.novelty?' · '+(novelty[e.novelty]??e.novelty):''}</strong><p>{e.summary}</p>
        {e.entities?.length?<small>Entities: {e.entities.join(' · ')}</small>:null}<br/>
        {r.model&&<><small>Analysed by {r.model}</small><br/></>}
        <small>Uncertainty: {e.uncertainties.join(' · ')}</small>
        <p>{e.source_ids.map(id=>{const item=d.items.find(i=>i.id===id);return item?<a key={id} href={item.url} target="_blank" rel="noreferrer">{item.source} source </a>:<span key={id}>Source {id.slice(0,12)} </span>;})}</p>
      </article>))}
      <p className="panel-source">{d.total_items} observations · {d.model} · Statements about market effects are hypotheses.</p>
    </>}
  </section>;
}
