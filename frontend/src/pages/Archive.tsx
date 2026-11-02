import {ArrowUpRight} from 'lucide-react';
import type {Route} from '../app/routes';
import {Card,Empty,Loading,PageHead,money,stamp,useResource} from '../components/ui';

const NAMES:Record<string,string>={'acct-a':'A Careful','acct-b':'B Steady','acct-c':'C Bold','acct-d':'D Aggressive',
  'acct-trend':'Trend Rider','acct-range':'Range Trader','acct-breakout':'Breakout Hunter','acct-carry':'Funding Collector',
  'acct-combined':'Combined Bot','acct-trend-bot':'Trend-Led Bot','acct-master':'Master Bot'};
type Retired={id:string;status:string;retired_at:string|null;equity:number|null;trades:number};

export default function Archive({go}:{go:(r:Route)=>void}){
  const accounts=useResource<{retired:Retired[]}>('rules/accounts',300000);
  const rows=Array.isArray(accounts.data?.retired)?accounts.data!.retired:[];
  return <div className="page-narrow">
    <PageHead title="Trading history" description="Archived. Paper trading was switched off on 2 November 2026; nothing was deleted."/>
    <div className="grid">
      <Card title="Why it stopped" className="span-3">
        <p>Every strategy was tested on four years of history. None was proven to beat holding Bitcoin after costs, so the project now
          explains the market instead of trading it. All paper accounts, their trades and their reviews are kept below.</p>
      </Card>
      <Card title="Past paper accounts" label="Past paper accounts" className="span-3">
        {accounts.loading&&!accounts.data?<Loading/>:!rows.length?<Empty title="No archived accounts"/>:
          <div className="table-scroll"><table><thead><tr><th>Account</th><th>Retired</th><th>Final balance</th><th>Trades</th></tr></thead>
            <tbody>{rows.map(r=><tr key={r.id}><td>{NAMES[r.id]??r.id}</td><td>{stamp(r.retired_at)}</td><td>{money(r.equity)}</td><td>{r.trades}</td></tr>)}</tbody></table></div>}
      </Card>
      <Card title="Old pages" className="span-3">
        <ul className="more-list" aria-label="Archived pages">
          <li><button type="button" onClick={()=>go('rules')}><span><strong>Rule engine journal</strong><small className="muted">Every trigger, setup and trade the rule engine recorded.</small></span><ArrowUpRight size={16} aria-hidden/></button></li>
          <li><button type="button" onClick={()=>go('paper')}><span><strong>Old paper account</strong><small className="muted">The first model's paper account (it never traded).</small></span><ArrowUpRight size={16} aria-hidden/></button></li>        </ul>
      </Card>
    </div>
  </div>;
}
