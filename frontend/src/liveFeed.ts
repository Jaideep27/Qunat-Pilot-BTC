import type {Candle} from './api';

export const liveTimeframes=['5m','15m','30m','1h','4h','1d','1w'] as const;
export type LiveTick={bar:Candle;timeframe:string;eventAt:number;receivedAt:number;closed:boolean};
export type LiveState={status:'connecting'|'live'|'reconnecting'|'stale'|'disconnected';eventAt?:number;receivedAt?:number};

// Exchange data goes straight to the chart. Only the owner collector writes PostgreSQL.
export function parseKline(raw:string,timeframe:string,receivedAt=Date.now()):LiveTick|null{
  try{
    const message=JSON.parse(raw),data=message.data??message,k=data.k;
    if(data.e!=='kline'||data.s!=='BTCUSDT'||k?.s!=='BTCUSDT'||k.i!==timeframe||typeof k.x!=='boolean')return null;
    const [open,high,low,close,volume]=[k.o,k.h,k.l,k.c,k.v].map(Number);
    if(![open,high,low,close,volume,k.t,k.T,data.E].every(Number.isFinite)
      ||Math.min(open,high,low,close)<=0||volume<0||high<Math.max(open,close,low)||low>Math.min(open,close)
      ||k.t<=0||k.T<k.t||data.E<k.t||data.E>receivedAt+30000||receivedAt-data.E>30000)return null;
    return {timeframe,eventAt:data.E,receivedAt,closed:k.x,bar:{event_time:new Date(k.t).toISOString(),
      available_at:new Date(k.T+1).toISOString(),open,high,low,close,volume,complete:k.x,
      source:'Binance USD-M perpetual WebSocket'}};
  }catch{return null}
}

export function subscribeKlines(timeframe:string,onTick:(tick:LiveTick)=>void,onState:(state:LiveState)=>void){
  if(!(liveTimeframes as readonly string[]).includes(timeframe))throw new Error('Unsupported live timeframe');
  let stopped=false,socket:WebSocket|undefined,retry:ReturnType<typeof setTimeout>|undefined;
  let failures=0,lastMessageAt=Date.now(),lastTick:LiveTick|null=null;
  const reconnect=()=>{
    if(stopped||retry)return;
    onState({status:'reconnecting'});
    const delay=Math.min(30000,1000*2**Math.min(failures++,5));
    retry=setTimeout(()=>{retry=undefined;connect()},delay);
  };
  const connect=()=>{
    if(stopped)return;
    lastMessageAt=Date.now();onState({status:failures?'reconnecting':'connecting'});
    try{
      const current=new WebSocket(`wss://fstream.binance.com/market/ws/btcusdt@kline_${timeframe}`);socket=current;
      current.onmessage=event=>{
        if(stopped||socket!==current)return;
        const tick=parseKline(event.data,timeframe);if(!tick)return;
        if(lastTick&&(tick.bar.event_time<lastTick.bar.event_time||tick.eventAt<lastTick.eventAt
          ||(tick.bar.event_time===lastTick.bar.event_time&&lastTick.closed&&!tick.closed)))return;
        lastTick=tick;lastMessageAt=tick.receivedAt;failures=0;
        onTick(tick);onState({status:'live',eventAt:tick.eventAt,receivedAt:tick.receivedAt});
      };
      current.onerror=()=>{if(socket===current)current.close()};
      current.onclose=()=>{if(!stopped&&socket===current){socket=undefined;reconnect()}};
    }catch{reconnect()}
  };
  const watchdog=setInterval(()=>{
    if(stopped||Date.now()-lastMessageAt<=10000)return;
    onState({status:'stale',eventAt:lastTick?.eventAt,receivedAt:lastTick?.receivedAt});
    if(socket){const old=socket;socket=undefined;old.close()}
    reconnect();
  },1000);
  connect();
  return ()=>{stopped=true;clearInterval(watchdog);if(retry)clearTimeout(retry);socket?.close()};
}
