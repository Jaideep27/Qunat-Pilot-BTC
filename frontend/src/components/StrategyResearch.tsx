import {Card,Collapsible,Loading,Pill,Term,num,pct,useResource} from './ui';

type Family={id:string;track:string;kind:string;configurations:number;calls:number;call_rate:number|null;hit_rate:number|null;
  wilson_low:number|null;wilson_high:number|null;break_even:number|null;mean_net:number|null;deflated_sharpe:number;
  pbo:number|null;passed:boolean;failed:string[]};
type Feed={rows:number;first:string|null;last:string|null};
type AgentFamily={configurations:number;pbo:number|null;passed:boolean;failed:string[];calls:number;hit_rate:number|null;
  wilson_low:number|null;wilson_high:number|null;break_even:number|null;mean_net:number|null};
type AgentConfig={id:string;trades:number;hit_rate:number|null;break_even:number|null;mean_net:number|null;account_return:number|null;
  max_drawdown_closed:number|null;exit_reasons:Record<string,number>};
type Data={status:string;run:string|null;trials:Record<string,number>;passing:string[];holdout_evaluated:boolean;
  next_test:string;families:Family[];forward:Record<string,Feed>;
  agent?:{run:string;trials:number;families:Record<string,AgentFamily>;configurations:AgentConfig[]}|null};
const names:Record<string,string>={S1:'Breakout',S2:'Momentum agreement',S3:'Funding crowding',S4:'Volatility squeeze',
  S5:'Coinbase premium',I1:'Flush-out rebound',I2:'Crowding fade',I3:'Session momentum',I4:'Taker flow',I5:'Coinbase premium (hourly)',
  M1:'Trend + meta-label',M2:'24h momentum + meta-label'};
const rules:Record<string,string>={B0:'Coin flip (control)',B1:'Trend (1h & 4h agree)',B2:'Best plan, either side',B3:'Pullback in trend'};
const unit=(track:string)=>track==='swing'?'days':'hours';
const ci=(low:number|null,high:number|null)=>`${low==null?'—':(low*100).toFixed(1)}–${pct(high)}`;

function HitBar({hit,breakEven}:{hit:number|null;breakEven:number|null}){
  // Hit rate against break-even: green only when the rule won more often than it needed to.
  if(hit==null)return null;
  return <span className="hitbar" aria-hidden><i style={{width:hit*100+'%'}} className={breakEven!=null&&hit>breakEven?'tone-bg-up':'tone-bg-down'}/>
    {breakEven!=null&&<b style={{left:breakEven*100+'%'}}/>}</span>;
}

export default function StrategyResearch(){
  const resource=useResource<Data>('strategy-research',300000);
  const data=resource.data;
  const total=Object.values(data?.trials??{}).reduce((a,b)=>a+b,0);
  return <Card title="Strategy research" label="Strategy research" tag="Pre-registered · never trades">
    {resource.error&&<p role="status">Strategy research unavailable.</p>}
    {!data&&!resource.error&&<Loading/>}
    {data?.status==='not_run'&&<p>No strategy research run yet. Run <code>quantpilot research-strategies</code>.</p>}
    {data?.status==='complete'&&<>
      <p className="lead"><strong>{data.passing?.length?`Passed: ${data.passing.join(', ')}`:'No strategy passed.'}</strong> {total} configurations tested
        walk-forward, scored as trades after fees and funding. <Term k="hit_rate">Hit rate</Term> is the share of calls that made money,
        shown with <Term k="call_rate">how often the rule calls</Term>; the bar's marker is the <Term k="break_even">break-even</Term>.</p>
      <div className="table-scroll"><table aria-label="Strategy families"><thead><tr><th>Strategy</th><th>Calls</th><th>How often it calls</th><th>Hit rate (95% CI)</th>
        <th>Break-even</th><th>Net per call</th><th>Verdict</th></tr></thead><tbody>{(data.families??[]).map(f=><tr key={f.id}>
        <td><strong>{names[f.id]??f.id}</strong> <small className="muted">{f.track}</small></td><td>{f.calls}</td><td>{pct(f.call_rate)} of {unit(f.track)}</td>
        <td><span className="hitcell">{pct(f.hit_rate)} ({ci(f.wilson_low,f.wilson_high)})<HitBar hit={f.hit_rate} breakEven={f.break_even}/></span></td>
        <td>{pct(f.break_even)}</td><td className={(f.mean_net??0)>0?'tone-up':'tone-down'}>{pct(f.mean_net,2)}</td>
        <td>{f.passed?<Pill tone="ok">Passed</Pill>:<span className="fail">Failed: {f.failed.map(c=>c.replaceAll('_',' ')).join(' · ')}</span>}</td></tr>)}</tbody></table></div>
      {data.agent&&<Collapsible title="Trading-agent trade plans (step B)" label="Trading agent results" defaultOpen
        summary={data.agent.families.TP?(data.agent.families.TP.passed?'Passed':`Failed · ${pct(data.agent.families.TP.hit_rate)} hit vs ${pct(data.agent.families.TP.break_even)} break-even`):undefined}>
        <p className="note">Structure-based plans (stop behind support/resistance, target at the next level, 0.25% risk) entered by four rules, run hourly from 2021-10 to 2025-10 in the futures simulator after all costs.</p>
        <div className="table-scroll"><table aria-label="Trade plan configurations"><thead><tr><th>Entry rule</th><th>Min R:R</th><th>Trades</th><th>Hit rate</th><th>Break-even</th>
          <th>Net per trade</th><th>Account</th><th>Max drawdown</th></tr></thead><tbody>{data.agent.configurations.map(c=>{const [rule,rr]=c.id.split(':rr');return <tr key={c.id}>
          <td>{rules[rule]??rule}</td><td>{rr}</td><td>{c.trades}</td><td><span className="hitcell">{pct(c.hit_rate)}<HitBar hit={c.hit_rate} breakEven={c.break_even}/></span></td>
          <td>{pct(c.break_even)}</td><td className={(c.mean_net??0)>0?'tone-up':'tone-down'}>{pct(c.mean_net,2)}</td>
          <td className={(c.account_return??0)>0?'tone-up':'tone-down'}>{pct(c.account_return,1)}</td><td>{pct(c.max_drawdown_closed,1)}</td></tr>})}</tbody></table></div>
      </Collapsible>}
      <dl className="facts facts-grid">{Object.entries(data.forward??{}).map(([name,feed])=><div key={name}>
        <dt><Term k={name==='depth'?'depth':'liquidations'}>{name==='depth'?'Order-book depth':'Liquidations'} (forward only)</Term></dt>
        <dd>{feed.rows?`${num(feed.rows,0)} ${name==='depth'?'snapshots':'minutes with liquidations'}`:'not recording yet'}</dd></div>)}
        <div><dt>Next pre-registered test</dt><dd>{data.next_test}</dd></div>
        <div><dt><Term k="holdout">Holdout</Term></dt><dd>{data.holdout_evaluated?'Evaluated once':'Holdout year untouched'}</dd></div></dl>
      <p className="source">A pass needs enough calls, a hit rate whose lower bound beats break-even, a <Term k="deflated_sharpe">deflated Sharpe</Term> of at least 0.95
        given every configuration tried, <Term k="pbo">PBO</Term> below 0.3 and most years positive. Details: docs/research/strategy-research.md and docs/research/trading-agent-research.md.</p>
    </>}
  </Card>;
}
