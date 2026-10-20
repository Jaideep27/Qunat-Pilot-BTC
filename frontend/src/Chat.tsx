import {useEffect,useState} from 'react';
import {post,request} from './api';

type Answer={id:string;headline:{kind:string;probability:number|null;base_rate:number|null;method:string;confidence:string};
  range:{low:number;high:number;coverage:number}|null;warnings:string[]};
type Message={role:string;text:string;tools?:string[];answers?:Answer[];ai_status?:string};
type Thread={id:string;title:string};
const labels:Record<string,string>={no_edge:'No edge (about a coin flip)',weak:'Weak evidence',validated:'Validated'};
const chips=['Up or down next 1h?','Up or down next 4h?','Up or down next 24h?','Chance of a 5% drop today?','Why is BTC moving?'];
const pct=(n:number|null|undefined)=>n==null?'—':(n*100).toFixed(1)+'%';
const usd=(n:number)=>'$'+Math.round(n).toLocaleString('en-US');

function Card({a}:{a:Answer}){return <div className="answer-card">
  {a.headline.kind==='probability'&&<strong>{pct(a.headline.probability)}</strong>}
  {a.range&&<span> · {Math.round(a.range.coverage*100)}% range {usd(a.range.low)} – {usd(a.range.high)}</span>}
  <small> · {labels[a.headline.confidence]??a.headline.confidence} · {a.headline.method.replaceAll('_',' ')}</small>
  {a.warnings?.map(w=><p key={w} className="control-note">{w}</p>)}</div>}

export default function Chat(){
  const [threads,setThreads]=useState<Thread[]>([]),[thread,setThread]=useState<string|null>(null);
  const [messages,setMessages]=useState<Message[]>([]),[text,setText]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const loadThreads=()=>request<Thread[]>('chat/threads').then(r=>setThreads(Array.isArray(r.data)?r.data:[])).catch(()=>{});
  useEffect(()=>{void loadThreads()},[]);
  const open=async(id:string)=>{setThread(id);const r=await request<{messages:Message[]}>('chat/threads/'+id);setMessages(r.data.messages)};
  const send=async()=>{if(!text.trim()||busy)return;const message=text;setText('');setBusy(true);setError('');
    setMessages(m=>[...m,{role:'user',text:message}]);
    try{const r=await post<{thread_id:string}>('chat',{message,thread_id:thread});await open(r.data.thread_id);void loadThreads()}
    catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}};
  const quick=async(q:string)=>{setBusy(true);setError('');
    try{const r=await post<Answer>('ask',{question:q,ai:false});
      setMessages(m=>[...m,{role:'user',text:q},{role:'assistant',text:'Quant-only answer',answers:[r.data]}])}
    catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}};
  return <section className="panel chat-panel" aria-label="QuantPilot chat">
    <div className="panel-heading"><h2>Chat</h2><span className="subtle-tag">CLAUDE · QUANTPILOT TOOLS ONLY · NEVER TRADES</span></div>
    <div className="chat-layout"><aside className="chat-threads"><button onClick={()=>{setThread(null);setMessages([])}}>New chat</button>
      {threads.map(t=><button key={t.id} className={t.id===thread?'active':''} onClick={()=>void open(t.id)}>{t.title}</button>)}</aside>
    <div className="chat-main"><div className="chat-chips">{chips.map(c=><button key={c} disabled={busy} onClick={()=>void quick(c)}>{c}</button>)}</div>
      <div className="chat-messages">{!messages.length&&<p className="control-note">Ask about BTC direction, price levels, ranges, big moves, or why it is moving. Answers show their confidence and are scored later.</p>}
        {messages.map((m,i)=><div key={i} className={'chat-message '+m.role}><p>{m.text}</p>
          {m.tools?.length?<small className="chat-tools">Used: {m.tools.join(' · ')}</small>:null}
          {m.answers?.map(a=><Card key={a.id} a={a}/>)}</div>)}
        {busy&&<p className="control-note">Thinking with QuantPilot tools…</p>}
        {error&&<p role="status">{error}</p>}</div>
      <form className="chat-input" onSubmit={e=>{e.preventDefault();void send()}}>
        <textarea aria-label="Message" maxLength={2000} value={text} onChange={e=>setText(e.target.value)} placeholder="Will BTC go up in the next 4 hours?"/>
        <button type="submit" disabled={busy||!text.trim()}>Send</button></form></div></div>
  </section>;
}
