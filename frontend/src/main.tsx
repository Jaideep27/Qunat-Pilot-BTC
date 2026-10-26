import React,{useCallback,useEffect,useLayoutEffect,useMemo,useRef,useState,lazy,Suspense} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity,ChevronsLeft,ChevronsRight,Command,CornerDownLeft,Search} from 'lucide-react';
import {routes,fromHash,type Route} from './app/routes';
import {preview,type Candle} from './lib/api';
import {subscribeKlines} from './lib/liveFeed';
import {Boundary,Loading,money,signedPct,useFlash,useResource} from './components/ui';
import './styles/styles.css';

const Overview=lazy(()=>import('./pages/Overview'));
const TradingWorkspace=lazy(()=>import('./components/TradingWorkspace'));
const Ask=lazy(()=>import('./pages/Ask'));
const Research=lazy(()=>import('./pages/Research'));
const Paper=lazy(()=>import('./pages/Paper'));
const Rules=lazy(()=>import('./pages/Rules'));
const News=lazy(()=>import('./pages/News'));
const System=lazy(()=>import('./pages/System'));

const safeRead=(key:string,fallback:string)=>{try{return localStorage.getItem(key)??fallback}catch{return fallback}};
const safeWrite=(key:string,value:string)=>{try{localStorage.setItem(key,value)}catch{/* storage may be unavailable */}};

/** A small price line from the stored candles plus live ticks. */
function Sparkline({values}:{values:number[]}){
  if(values.length<2)return <svg className="spark" aria-hidden viewBox="0 0 80 24"/>;
  const lo=Math.min(...values),hi=Math.max(...values),span=hi-lo||1;
  const points=values.map((v,i)=>`${(i/(values.length-1))*80},${22-((v-lo)/span)*20}`).join(' ');
  const up=values.at(-1)!>=values[0];
  return <svg className={'spark '+(up?'spark-up':'spark-down')} aria-hidden viewBox="0 0 80 24" preserveAspectRatio="none">
    <polyline points={points} fill="none" strokeWidth="1.6" vectorEffect="non-scaling-stroke"/></svg>;
}

function LivePrice(){
  const candles=useResource<Candle[]>('candles',60000);
  const [live,setLive]=useState<{price:number;status:string}|null>(null);
  const [ticks,setTicks]=useState<number[]>([]);
  useEffect(()=>{
    if(preview)return;
    return subscribeKlines('5m',tick=>{setLive(l=>({price:tick.bar.close,status:l?.status??'live'}));
      setTicks(t=>[...t.slice(-59),tick.bar.close])},
      state=>setLive(l=>({price:l?.price??NaN,status:state.status})));
  },[]);
  const bars=Array.isArray(candles.data)?candles.data:[];   // an unexpected payload must never blank the app
  const last=bars.at(-1)?.close,first=bars[0]?.open;
  const price=live&&Number.isFinite(live.price)?live.price:last;
  const change=price&&first?price/first-1:undefined;
  const streaming=live?.status==='live';
  const flash=useFlash(price);
  const series=useMemo(()=>[...bars.slice(-48).map(b=>b.close),...ticks].filter(Number.isFinite),[bars,ticks]);
  return <div className="ticker" aria-label="Bitcoin price">
    <span className="coin" aria-hidden>₿</span>
    <div className="ticker-text"><strong className={flash}>{money(price)}</strong><small className={change==null?'':change>=0?'tone-up':'tone-down'}>{signedPct(change)} 24h</small></div>
    <Sparkline values={series}/>
    <span className={'live-dot '+(streaming?'on':'')} title={streaming?'Live exchange stream':'Latest stored candle'}/>
  </div>;
}

function Clock(){
  const [now,setNow]=useState(()=>new Date());
  useEffect(()=>{const t=setInterval(()=>setNow(new Date()),1000);return()=>clearInterval(t)},[]);
  return <span className="clock" title="Coordinated Universal Time">{now.toISOString().slice(11,19)} <small>UTC</small></span>;
}

function AskBox({onAsk}:{onAsk:(q:string)=>void}){
  const [text,setText]=useState('');
  const input=useRef<HTMLInputElement>(null);
  useEffect(()=>{
    const key=(e:KeyboardEvent)=>{
      const typing=e.target instanceof HTMLElement&&['INPUT','TEXTAREA'].includes(e.target.tagName);
      if(e.key==='/'&&!typing){e.preventDefault();input.current?.focus()}
    };
    addEventListener('keydown',key);return()=>removeEventListener('keydown',key);
  },[]);
  return <form className="askbox" role="search" onSubmit={e=>{e.preventDefault();if(text.trim()){onAsk(text.trim());setText('')}}}>
    <Search size={15} aria-hidden/>
    <input ref={input} aria-label="Ask a question" placeholder="Ask anything… e.g. chance BTC touches 90k this week" value={text}
      onChange={e=>setText(e.target.value)} maxLength={2000}/>
    <kbd aria-hidden>/</kbd>
  </form>;
}

type Action={id:string;label:string;hint:string;run:()=>void};

/** Ctrl/⌘+K: jump to any page or ask a question from anywhere. */
function Palette({open,onClose,go,ask}:{open:boolean;onClose:()=>void;go:(r:Route)=>void;ask:(q:string)=>void}){
  const [query,setQuery]=useState('');
  const [index,setIndex]=useState(0);
  const input=useRef<HTMLInputElement>(null);
  useEffect(()=>{if(open){setQuery('');setIndex(0);requestAnimationFrame(()=>input.current?.focus())}},[open]);
  // Escape closes from anywhere: focus reaches the search box a frame late, and a click can move it away.
  useEffect(()=>{
    if(!open)return;
    const close=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.preventDefault();onClose()}};
    window.addEventListener('keydown',close);
    return ()=>window.removeEventListener('keydown',close);
  },[open,onClose]);
  const actions=useMemo<Action[]>(()=>{
    const q=query.trim().toLowerCase();
    const pages=routes.map((r,i)=>({id:r.id,label:'Go to '+r.label,hint:'Alt+'+(i+1),run:()=>go(r.id)}))
      .filter(a=>!q||a.label.toLowerCase().includes(q));
    return query.trim()?[...pages,{id:'ask',label:`Ask: “${query.trim()}”`,hint:'Enter',run:()=>ask(query.trim())}]:pages;
  },[query,go,ask]);
  if(!open)return null;
  const choose=(a?:Action)=>{if(a){onClose();a.run()}};
  return <div className="palette-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}>
    <div className="palette" role="dialog" aria-modal="true" aria-label="Command palette">
      <div className="palette-input"><Command size={16} aria-hidden/>
        <input ref={input} aria-label="Search pages or ask" placeholder="Jump to a page or ask a question…" value={query}
          onChange={e=>{setQuery(e.target.value);setIndex(0)}}
          onKeyDown={e=>{
            if(e.key==='ArrowDown'){e.preventDefault();setIndex(i=>Math.min(actions.length-1,i+1))}
            else if(e.key==='ArrowUp'){e.preventDefault();setIndex(i=>Math.max(0,i-1))}
            else if(e.key==='Enter'){e.preventDefault();choose(actions[index])}
          }}/><kbd aria-hidden>Esc</kbd></div>
      <ul className="palette-list" role="listbox" aria-label="Results">
        {actions.map((a,i)=><li key={a.id} role="option" aria-selected={i===index} className={i===index?'on':''}
          onMouseEnter={()=>setIndex(i)} onMouseDown={e=>{e.preventDefault();choose(a)}}>
          <span>{a.label}</span><kbd aria-hidden>{i===index?<CornerDownLeft size={11}/>:a.hint}</kbd></li>)}
        {!actions.length&&<li className="palette-empty">No page matches. Type a question to ask it.</li>}
      </ul>
    </div>
  </div>;
}

// Fetch the other pages' code once the first page is idle, so switching tabs is instant.
const prefetch=()=>[()=>import('./pages/Research'),()=>import('./pages/Ask'),()=>import('./pages/Paper'),()=>import('./pages/Rules'),()=>import('./pages/News'),
  ()=>import('./pages/System'),()=>import('./components/TradingWorkspace')].forEach(load=>void load().catch(()=>{}));

function App(){
  const [route,setRoute]=useState<Route>(fromHash);
  const [seed,setSeed]=useState<{text:string;id:number}|null>(null);
  const [narrow,setNarrow]=useState(()=>safeRead('qp:sidebar','wide')==='narrow');
  const [palette,setPalette]=useState(false);
  const health=useResource<{status:string}>('health',60000);
  const nav=useRef<HTMLDivElement>(null);
  const [indicator,setIndicator]=useState<React.CSSProperties>({opacity:0});
  useEffect(()=>{const t=setTimeout(prefetch,2500);return()=>clearTimeout(t)},[]);
  useEffect(()=>{const sync=()=>setRoute(fromHash());addEventListener('hashchange',sync);return()=>removeEventListener('hashchange',sync)},[]);
  const go=useCallback((next:Route)=>{if('#'+next!==location.hash)history.pushState(null,'','#'+next);setRoute(next);scrollTo({top:0})},[]);
  const ask=useCallback((text:string)=>{setSeed({text,id:Date.now()});go('ask')},[go]);
  useEffect(()=>{safeWrite('qp:sidebar',narrow?'narrow':'wide')},[narrow]);
  useEffect(()=>{
    const key=(e:KeyboardEvent)=>{
      if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();setPalette(p=>!p);return}
      if(e.altKey&&!e.ctrlKey&&!e.metaKey&&/^[1-9]$/.test(e.key)){const r=routes[Number(e.key)-1];if(r){e.preventDefault();go(r.id)}}
    };
    addEventListener('keydown',key);return()=>removeEventListener('keydown',key);
  },[go]);
  // The active-tab highlight slides to the selected item; re-measured whenever the sidebar changes size.
  useLayoutEffect(()=>{
    const host=nav.current;if(!host)return;
    const place=()=>{const el=host.querySelector<HTMLElement>('button[aria-current="page"]');
      setIndicator(el?{opacity:1,width:el.offsetWidth,height:el.offsetHeight,transform:`translate(${el.offsetLeft}px,${el.offsetTop}px)`}:{opacity:0})};
    place();
    const observer=new ResizeObserver(place);observer.observe(host);
    return()=>observer.disconnect();
  },[route,narrow]);
  return <div className={'shell'+(narrow?' nav-narrow':'')}>
    <div className="aurora" aria-hidden><i/><i/><i/></div>
    <nav className="sidebar" aria-label="Main navigation">
      <a className="brand" href="#overview" onClick={e=>{e.preventDefault();go('overview')}} aria-label="QuantPilot home">
        <span className="brand-mark"><Activity size={20}/></span><span className="brand-name">QuantPilot<small>BTC intelligence</small></span></a>
      <div className="nav-items" ref={nav}>
        <span className="nav-indicator" style={indicator} aria-hidden/>
        {routes.map((r,i)=>{const Icon=r.icon;return <button key={r.id} type="button" aria-label={r.label}
          aria-current={route===r.id?'page':undefined} className={route===r.id?'active':''} onClick={()=>go(r.id)} title={`${r.label} · Alt+${i+1}`}>
          <Icon size={18} aria-hidden/><span>{r.label}</span></button>})}</div>
      <div className="sidebar-foot"><span className="pill pill-accent">Paper only</span><small>Research & simulation. No real-money execution.</small></div>
      <button type="button" className="nav-collapse" onClick={()=>setNarrow(n=>!n)} aria-label={narrow?'Expand sidebar':'Collapse sidebar'}
        title={narrow?'Expand sidebar':'Collapse sidebar'}>{narrow?<ChevronsRight size={16}/>:<ChevronsLeft size={16}/>}</button>
    </nav>
    <div className="main-col">
      <header className="topbar">
        <Boundary name="The price ticker"><LivePrice/></Boundary>
        <AskBox onAsk={ask}/>
        <button type="button" className="palette-button" onClick={()=>setPalette(true)} aria-label="Open command palette" title="Command palette (Ctrl+K)">
          <Command size={14} aria-hidden/><span>Ctrl K</span></button>
        <Clock/>
        <a className="topbar-link" href="/docs" onClick={e=>{e.preventDefault();open('http://127.0.0.1:8000/docs','_blank')}}>API</a>
      </header>
      {preview&&<div className="banner banner-warn">INTERFACE PREVIEW · Synthetic values. No released model, measured results or paper trades.</div>}
      {health.error&&<div role="alert" className="banner banner-bad">QuantPilot services unavailable. Check the backend connection.
        <button type="button" onClick={health.reload}>Retry</button></div>}
      <main className="content">
        <Boundary key={route} name="This page"><Suspense fallback={<Loading lines={6}/>}>
          <div className="page-enter">
            {route==='overview'&&<Overview go={go} ask={ask}/>}
            {route==='chart'&&<><div className="page-head"><div><h1>Chart</h1><p>Persistent history, 86 indicator studies, drawing tools and a live exchange stream.</p></div></div><TradingWorkspace/></>}
            {route==='ask'&&<Ask seed={seed}/>}
            {route==='research'&&<Research/>}
            {route==='rules'&&<Rules/>}
            {route==='paper'&&<Paper/>}
            {route==='news'&&<News/>}
            {route==='system'&&<System/>}
          </div>
        </Suspense></Boundary>
      </main>
      <footer className="footer"><span>QuantPilot · BTCUSDT USD-M perpetual · research and paper simulation only</span>
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView Lightweight Charts™</a></footer>
    </div>
    <Palette open={palette} onClose={()=>setPalette(false)} go={go} ask={ask}/>
  </div>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
