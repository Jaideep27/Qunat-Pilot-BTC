import {Card,Empty,Loading,PageHead,money,stamp,useResource} from '../components/ui';

const NAMES:Record<string,string>={'acct-a':'A Careful','acct-b':'B Steady','acct-c':'C Bold','acct-d':'D Aggressive',
  'acct-trend':'Trend Rider','acct-range':'Range Trader','acct-breakout':'Breakout Hunter','acct-carry':'Funding Collector',
  'acct-combined':'Combined Bot','acct-trend-bot':'Trend-Led Bot','acct-master':'Master Bot','experimental-v1':'Rulebook reference account'};
type Account={id:string;status:string;retired_at:string|null;equity:number|null;trades:number};

export default function Archive(){
  const archive=useResource<{accounts:Account[]}>('archive',600000);
  const rows=Array.isArray(archive.data?.accounts)?archive.data!.accounts:[];
  return <div className="page-narrow">
    <PageHead title="Trading history" description="Paper trading was switched off on 2 November 2026. The accounts are kept here for the record."/>
    <div className="grid">
      <Card title="Why it stopped" className="span-3">
        <p>Every strategy was tested on four years of history. None was proven to beat holding Bitcoin after costs, so the project now
          explains the market instead of trading it. The research results are on the Research page and in the project docs.</p>
      </Card>
      <Card title="Past paper accounts" label="Past paper accounts" className="span-3">
        {archive.loading&&!archive.data?<Loading/>:!rows.length?<Empty title="No archived accounts"/>:
          <div className="table-scroll"><table><thead><tr><th>Account</th><th>Status</th><th>Stopped</th><th>Final balance</th><th>Trades</th></tr></thead>
            <tbody>{rows.map(r=><tr key={r.id}><td>{NAMES[r.id]??r.id}</td><td>{r.status.toLowerCase()}</td><td>{stamp(r.retired_at)}</td>
              <td>{money(r.equity)}</td><td>{r.trades}</td></tr>)}</tbody></table></div>}
      </Card>
    </div>
  </div>;
}
