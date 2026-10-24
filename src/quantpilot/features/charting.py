"""Chart studies on completed bars. This catalogue does not retune the research model."""
import numpy as np
import pandas as pd
from quantpilot.features.core import technical, div


def studies(rows,selected=None):
    if not rows:return dict(catalogue=[],series={})
    d=pd.DataFrame(rows)
    d.index=pd.to_datetime(d.available_at,utc=True)
    f=technical(d)
    overlay={}
    for n in (9,21,50,200):overlay[f'EMA {n}']=d.close.ewm(span=n,adjust=False,min_periods=n).mean()
    for n in (20,50,200):overlay[f'SMA {n}']=d.close.rolling(n).mean()
    center=d.close.rolling(20).mean();std=d.close.rolling(20).std(ddof=0)
    overlay.update({'Bollinger upper':center+2*std,'Bollinger middle':center,'Bollinger lower':center-2*std,
        'Keltner upper':d.close.ewm(span=20,adjust=False).mean()+2*f.atr,
        'Keltner lower':d.close.ewm(span=20,adjust=False).mean()-2*f.atr,
        'Donchian high':d.high.shift().rolling(20).max(),'Donchian low':d.low.shift().rolling(20).min()})
    session=d.index.floor('D')
    overlay['Session VWAP']=div((((d.high+d.low+d.close)/3)*d.volume).groupby(session).cumsum(),d.volume.groupby(session).cumsum())
    macd=d.close.ewm(span=12,adjust=False).mean()-d.close.ewm(span=26,adjust=False).mean()
    f['MACD line']=macd;f['MACD signal']=macd.ewm(span=9,adjust=False).mean()
    f['MACD histogram']=macd-f['MACD signal']
    f['OBV']=(np.sign(d.close.diff()).fillna(0)*d.volume).cumsum()
    f['Taker buy imbalance']=div(2*d.taker_buy_volume-d.volume,d.volume)
    all_series={**{name:(value,'overlay') for name,value in overlay.items()},
                **{name:(f[name],'oscillator') for name in f}}
    catalogue=[];output={}
    times=pd.to_datetime(d.event_time,utc=True).astype('int64').to_numpy()//10**9
    for name,(values,pane) in all_series.items():
        label=name.replace('_',' ').title() if name not in overlay else name
        catalogue.append(dict(id=name,label=label,pane=pane))
        if selected is not None and name not in selected:continue
        numeric=values.to_numpy(dtype=float)
        valid=np.isfinite(numeric)
        output[name]=[dict(time=int(t),value=float(value)) for t,value in zip(times[valid],numeric[valid])]
    return dict(catalogue=catalogue,series=output,
                definitions='Completed candles only; EMA uses adjust=False; Bollinger uses close, 20 bars and population standard deviation; session VWAP resets at UTC midnight. Feature distances are ATR-normalized where labeled.')
