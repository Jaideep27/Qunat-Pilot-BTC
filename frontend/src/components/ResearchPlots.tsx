import {ResponsiveContainer,LineChart,Line,XAxis,YAxis,CartesianGrid,Tooltip,ReferenceLine} from 'recharts';
import type {Comparison} from '../lib/api';

export default function ResearchPlots({comparison}:{comparison:Comparison}){
  let peak=10000;
  const drawdown=comparison.trading.curve.map(point=>{
    peak=Math.max(peak,point.equity);return {time:point.time,drawdown:100*(point.equity/peak-1)};
  });
  return <>
    <div className="two-col"><section className="panel"><div className="panel-heading"><h2>Holdout equity · {comparison.group}</h2></div>
      <div className="research-chart"><ResponsiveContainer width="100%" height={260}><LineChart data={comparison.trading.curve}>
        <CartesianGrid stroke="#25303b"/><XAxis dataKey="time" hide/><YAxis domain={['auto','auto']} stroke="#7e8b9e"/>
        <Tooltip contentStyle={{background:'#10151d',border:'1px solid #27313e'}}/><Line type="monotone" dataKey="equity" stroke="#4bd6ad" dot={false}/>
      </LineChart></ResponsiveContainer></div></section>
      <section className="panel"><div className="panel-heading"><h2>Probability calibration</h2></div>
        <ResponsiveContainer width="100%" height={260}><LineChart data={comparison.holdout.calibration}>
          <CartesianGrid stroke="#25303b"/><XAxis dataKey="predicted" type="number" domain={[0,1]} stroke="#7e8b9e"/>
          <YAxis domain={[0,1]} stroke="#7e8b9e"/><Tooltip/>
          <ReferenceLine segment={[{x:0,y:0},{x:1,y:1}]} stroke="#69768b" strokeDasharray="3 3"/><Line dataKey="observed" stroke="#b0a3fc"/>
        </LineChart></ResponsiveContainer></section></div>
    <section className="panel"><div className="panel-heading"><h2>Drawdown: hourly equity samples</h2></div>
      <ResponsiveContainer width="100%" height={190}><LineChart data={drawdown}>
        <CartesianGrid stroke="#25303b"/><XAxis dataKey="time" hide/><YAxis unit="%" stroke="#7e8b9e"/>
        <Tooltip contentStyle={{background:'#10151d',border:'1px solid #27313e'}}/><Line dataKey="drawdown" stroke="#ed7d80" dot={false}/>
      </LineChart></ResponsiveContainer>
      <div className="panel-source">Chart uses hourly equity samples; reported maximum drawdown uses all five-minute equity observations.</div>
    </section>
  </>;
}
