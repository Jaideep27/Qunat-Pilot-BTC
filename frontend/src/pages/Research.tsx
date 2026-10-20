import {lazy,Suspense,useState} from 'react';
import type {Research as ResearchData} from '../api';
import ShadowForecasts from '../ShadowForecasts';
import StrategyResearch from '../StrategyResearch';
import {Card,Collapsible,Empty,Loading,PageHead,Term,num,pct,useResource} from '../ui';

const ResearchPlots=lazy(()=>import('../ResearchPlots'));

function Release(){
  const backtests=useResource<ResearchData>('backtests',600000);
  const research=backtests.data;
  const [group,setGroup]=useState('full');
  const selected=research?.comparisons?.find(r=>r.group===group);
  return <Collapsible title="Original release model (2026)" label="Original release model"
    summary={research?.comparisons?.length?`${research.comparisons.length} feature groups · holdout ROC-AUC ${num(research.comparisons.find(c=>c.group==='full')?.holdout.roc_auc,3)} · 0 trades`:'Codex-era frozen model'}>
    {!research?<Loading/>:!research.comparisons?.length?<Empty title="Research has not been released">{research.reason??'Run the coverage audit and reproducible training workflow first.'}</Empty>:<>
      <p className="note">The frozen 60-minute direction model behind the paper account, evaluated on an untouched holdout (Aug–Sep 2026). It never cleared its own trading gates.</p>
      <div className="table-scroll"><table aria-label="Feature group comparison"><thead><tr><th>Features</th><th>Model</th><th><Term k="brier">Brier ↓</Term></th><th>ROC-AUC</th><th>Balanced accuracy</th><th>Net return</th><th>Drawdown</th></tr></thead>
        <tbody>{research.comparisons.map(c=><tr key={c.group} className={group===c.group?'selected-row':''} onClick={()=>setGroup(c.group)}>
          <td><button type="button" className="link" onClick={()=>setGroup(c.group)}>{c.group}</button></td><td>{c.kind}</td><td>{num(c.holdout.brier,4)}</td>
          <td>{num(c.holdout.roc_auc??undefined,3)}</td><td>{pct(c.holdout.balanced_accuracy,2)}</td><td>{pct(c.trading.net_return,2)}</td><td>{pct(c.trading.max_drawdown,2)}</td></tr>)}</tbody></table></div>
      {selected&&<Suspense fallback={<Loading/>}><ResearchPlots comparison={selected}/></Suspense>}
      <h3>Benchmarks over the same span</h3>
      <div className="table-scroll"><table aria-label="Benchmarks"><thead><tr><th>Strategy</th><th>Net return</th><th>Drawdown</th></tr></thead>
        <tbody>{Object.entries(research.benchmarks??{}).map(([name,b])=><tr key={name}><td>{name.replaceAll('_',' ')}</td><td>{pct(b.net_return,2)}</td><td>{pct(b.max_drawdown,2)}</td></tr>)}</tbody></table></div>
    </>}
  </Collapsible>;
}

export default function Research(){
  return <>
    <PageHead title="Research" description="What predicts BTC, tested honestly: forecasts scored forward, pre-registered strategies, and the original release model."/>
    <div className="stack">
      <Card title="What the research found" label="Research summary">
        <ul className="findings">
          <li><strong>Direction (1h–24h): no edge.</strong> Every model and strategy lost to the <Term k="base_rate">base rate</Term> or to costs out of sample.</li>
          <li><strong>Ranges and volatility: real skill.</strong> 1h and 4h <Term k="range80">80% ranges</Term> beat unconditional ranges; GARCH wins for 24h volatility.</li>
          <li><strong>Trade plans: no edge.</strong> Structure-based stops and targets did no better than a coin-flip side.</li>
          <li><strong>Still to test:</strong> live <Term k="liquidations">liquidation</Term> and <Term k="depth">order-book</Term> data, pre-registered for 2027-04-20.</li>
        </ul>
      </Card>
      <ShadowForecasts/>
      <StrategyResearch/>
      <Release/>
    </div>
  </>;
}
