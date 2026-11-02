import {Archive,BookOpen,CandlestickChart,FlaskConical,Gauge,House,LayoutGrid,MessageSquare,ServerCog,Workflow} from 'lucide-react';
import {publicView} from '../lib/api';

// Five tabs; everything else lives under More (and stays reachable by link and the Ctrl+K palette).
const all=[
  {id:'overview',label:'Home',icon:House,tab:true,about:''},
  {id:'indicators',label:'Indicators',icon:Gauge,tab:true,about:''},
  {id:'strategies',label:'Strategies',icon:Workflow,tab:true,about:''},
  {id:'chart',label:'Chart',icon:CandlestickChart,tab:true,about:''},
  {id:'more',label:'More',icon:LayoutGrid,tab:true,about:''},
  {id:'news',label:'News',icon:BookOpen,tab:false,about:'Headlines, AI news analysis and the economic calendar.'},
  {id:'ask',label:'Ask',icon:MessageSquare,tab:false,about:'Ask a question about BTC in plain words.'},
  {id:'research',label:'Research',icon:FlaskConical,tab:false,about:'What was tested, and what can and cannot be predicted.'},
  {id:'archive',label:'Trading history',icon:Archive,tab:false,about:'The retired paper accounts, kept for the record.'},
  {id:'system',label:'System',icon:ServerCog,tab:false,about:'Data feeds, services and backups.'},
] as const;
export type Route=typeof all[number]['id'];
// Ask runs on the owner's Claude subscription and shows their chat history, so the public view leaves it out.
export const routes=publicView?all.filter(r=>r.id!=='ask'):all;
export const tabs=routes.filter(r=>r.tab);
export const extras=routes.filter(r=>!r.tab);
export const tabOf=(r:Route):Route=>tabs.some(t=>t.id===r)?r:'more';
export const fromHash=():Route=>(routes.find(r=>'#'+r.id===location.hash)?.id??'overview');
