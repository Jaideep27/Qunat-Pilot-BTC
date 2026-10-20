import {useEffect,useRef,useState} from 'react';
import {post,request} from './api';

type Evidence=Record<string,{value:number|null;percentile:number|null;label:string}>;
type Forward={scored:number;independent:number;brier:number|null;base_brier:number|null;ai_brier:number|null;verdict:string};
type Answer={id:string;asked_at?:string;question?:{type:string;horizon_hours:number;side?:string};
  headline:{kind:string;probability:number|null;base_rate:number|null;method:string;confidence:string};
  range:{low:number;high:number;coverage:number}|null;warnings:string[];evidence?:Evidence;
  ai?:{status?:string;probability?:number|null};track_record?:{forward?:Forward|null};outcome?:{hit?:number;unscored?:boolean}};
type Message={role:string;text:string;tools?:string[];answers?:Answer[];ai_status?:string};
type Thread={id:string;title:string};
type Board={history:Answer[];scoreboard:Record<string,Forward>};
const labels:Record<string,string>={no_edge:'No edge (about a coin flip)',weak:'Weak evidence',validated:'Validated'};
const chips=['Up or down next 1h?','Up or down next 4h?','Up or down next 24h?','Chance of a 5% drop today?','Why is BTC moving?'];
const pct=(n:number|null|undefined)=>n==null?'—':(n*100).toFixed(1)+'%';
const usd=(n:number)=>'$'+Math.round(n).toLocaleString('en-US');
const ordinal=(n:number)=>{const r=Math.round(n),s=['th','st','nd','rd'],v=r%100;return r+(s[(v-20)%10]||s[v]||s[0])};
const brier=(n:number|null|undefined)=>n==null?'—':n.toFixed(3);

function Card({a}:{a:Answer}){
  const top=Object.values(a.evidence??{}).filter(e=>e.percentile!=null).slice(0,3);
  const forward=a.track_record?.forward;
  return <div className="answer-card">
    {a.headline.kind==='probability'&&<strong>{pct(a.headline.probability)}{a.question?.type==='direction'?(a.question.side==='down'?' chance of falling':' chance of rising'):''}</strong>}
    {a.range&&<span> · {Math.round(a.range.coverage*100)}% range {usd(a.range.low)} – {usd(a.range.high)}</span>}
    <small> · {labels[a.headline.confidence]??a.headline.confidence} · {a.headline.method.replaceAll('_',' ')}</small>
    {a.ai?.probability!=null&&<p className="control-note">Claude's view {pct(a.ai.probability)} (scored separately)</p>}
    {top.length>0&&<p className="control-note">{top.map(e=>`${e.label}: ${ordinal(e.percentile!)} percentile`).join(' · ')}</p>}
    <p className="control-note">Track record: {forward?`${forward.scored} scored · Brier ${brier(forward.brier)} vs base ${brier(forward.base_brier)} · ${forward.verdict}`:'no scored answers yet'}</p>
    {a.warnings?.map(w=><p key={w} className="control-note">{w}</p>)}</div>}

function Scoreboard({board}:{board:Board|null}){
  if(!board)return null;
  const rows=Object.entries(board.scoreboard);
  return <section className="panel" aria-label="Answer scoreboard">
    <div className="panel-heading"><h2>Answer scoreboard</h2><span className="subtle-tag">QUANT · CLAUDE · BASE RATE</span></div>
    {rows.length?<div className="table-scroll"><table><thead><tr><th>Question</th><th>Scored</th><th>Independent</th><th>Quant Brier</th><th>Claude Brier</th><th>Base Brier</th><th>Verdict</th></tr></thead>
      <tbody>{rows.map(([key,r])=><tr key={key}><td>{key}</td><td>{r.scored}</td><td>{r.independent}</td><td>{brier(r.brier)}</td><td>{brier(r.ai_brier)}</td><td>{brier(r.base_brier)}</td><td>{r.verdict}</td></tr>)}</tbody></table></div>
      :<p className="control-note">No answers have been scored yet.</p>}
    <h3>Recent answers</h3>
    <ul className="operations-checks">{board.history.slice(0,10).map(a=><li key={a.id}><span>{a.question?`${a.question.type} ${a.question.horizon_hours}h`:'answer'} · {pct(a.headline.probability)}</span>
      <strong>{a.outcome?.hit==null?(a.outcome?.unscored?'unscored':'pending'):(a.outcome.hit?'✓ happened':'✗ did not happen')}</strong></li>)}</ul>
    <p className="control-note">Lower Brier is better. Verdicts need at least 30 independent answers.</p>
  </section>;
}

export default function Chat({seed}:{seed?:{text:string;id:number}|null}={}){
  const [threads,setThreads]=useState<Thread[]>([]),[thread,setThread]=useState<string|null>(null);
  const [messages,setMessages]=useState<Message[]>([]),[text,setText]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [board,setBoard]=useState<Board|null>(null);
  const loadThreads=()=>request<Thread[]>('chat/threads').then(r=>setThreads(Array.isArray(r.data)?r.data:[])).catch(()=>{});
  const loadBoard=()=>request<Board>('answers?limit=20').then(r=>{if(r.data&&Array.isArray(r.data.history))setBoard(r.data)}).catch(()=>{});
  useEffect(()=>{void loadThreads();void loadBoard()},[]);
  const open=async(id:string)=>{setThread(id);const r=await request<{messages:Message[]}>('chat/threads/'+id);setMessages(r.data.messages)};
  const send=async()=>{if(!text.trim()||busy)return;const message=text;setText('');setBusy(true);setError('');
    setMessages(m=>[...m,{role:'user',text:message}]);
    try{const r=await post<{thread_id:string}>('chat',{message,thread_id:thread});await open(r.data.thread_id);void loadThreads();void loadBoard()}
    catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}};
  const quick=async(q:string)=>{setBusy(true);setError('');
    try{const r=await post<Answer>('ask',{question:q,ai:false});
      setMessages(m=>[...m,{role:'user',text:q},{role:'assistant',text:'Quant-only answer',answers:[r.data]}]);void loadBoard()}
    catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}};
  // A question typed in the top bar arrives here and gets an instant quant-only answer.
  const handled=useRef<number|null>(null);
  useEffect(()=>{if(seed?.text&&handled.current!==seed.id){handled.current=seed.id;void quick(seed.text)}},[seed?.id]);// eslint-disable-line react-hooks/exhaustive-deps
  return <>
  <section className="panel chat-panel" aria-label="QuantPilot chat">
    <div className="panel-heading"><h2>Chat</h2><span className="subtle-tag">CLAUDE · QUANTPILOT TOOLS ONLY · NEVER TRADES</span></div>
    <div className="chat-layout"><aside className="chat-threads"><button onClick={()=>{setThread(null);setMessages([])}}>New chat</button>
      {threads.map(t=><button key={t.id} className={t.id===thread?'active':''} onClick={()=>void open(t.id)}>{t.title}</button>)}</aside>
    <div className="chat-main"><div className="chat-chips">{chips.map(c=><button key={c} disabled={busy} onClick={()=>void quick(c)}>{c}</button>)}</div>
      <div className="chat-messages">{!messages.length&&<p className="control-note">Ask about BTC direction, price levels, ranges, big moves, or why it is moving. Answers show their confidence and are scored later.</p>}
        {messages.map((m,i)=><div key={i} className={'chat-message '+m.role}><p>{m.text}</p>
          {m.role==='assistant'&&m.ai_status&&m.ai_status!=='ok'&&<small className="chat-tools">AI unavailable: {m.ai_status.replaceAll('_',' ')}</small>}
          {m.tools?.length?<small className="chat-tools">Used: {m.tools.join(' · ')}</small>:null}
          {m.answers?.map(a=><Card key={a.id} a={a}/>)}</div>)}
        {busy&&<p className="control-note">Thinking with QuantPilot tools…</p>}
        {error&&<p role="status">{error}</p>}</div>
      <form className="chat-input" onSubmit={e=>{e.preventDefault();void send()}}>
        <textarea aria-label="Message" maxLength={2000} value={text} onChange={e=>setText(e.target.value)} placeholder="Will BTC go up in the next 4 hours?"/>
        <button type="submit" disabled={busy||!text.trim()}>Send</button></form></div></div>
  </section>
  <Scoreboard board={board}/>
  </>;
}
