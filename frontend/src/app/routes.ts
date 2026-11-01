import {BookOpen,Bot,CandlestickChart,FlaskConical,House,LayoutGrid,ListChecks,MessageSquare,ServerCog,Wallet} from 'lucide-react';
import {publicView} from '../lib/api';

// Four tabs; everything else lives under More (and stays reachable by link and the Ctrl+K palette).
const all=[
  {id:'overview',label:'Home',icon:House,tab:true,about:''},
  {id:'bots',label:'Bot',icon:Bot,tab:true,about:''},
  {id:'chart',label:'Chart',icon:CandlestickChart,tab:true,about:''},
  {id:'more',label:'More',icon:LayoutGrid,tab:true,about:''},
  {id:'ask',label:'Ask',icon:MessageSquare,tab:false,about:'Ask a question about BTC in plain words.'},
  {id:'news',label:'News',icon:BookOpen,tab:false,about:'Headlines and the economic calendar.'},
  {id:'research',label:'Research',icon:FlaskConical,tab:false,about:'What was tested, and what can and cannot be predicted.'},
  {id:'rules',label:'Rule engine',icon:ListChecks,tab:false,about:'The 20 support/resistance setups and the live journal.'},
  {id:'paper',label:'Old paper account',icon:Wallet,tab:false,about:'The first forward paper account from the frozen model.'},
  {id:'system',label:'System',icon:ServerCog,tab:false,about:'Data feeds, services and backups.'},
] as const;
export type Route=typeof all[number]['id'];
// Ask runs on the owner's Claude subscription and shows their chat history, so the public view leaves it out.
export const routes=publicView?all.filter(r=>r.id!=='ask'):all;
export const tabs=routes.filter(r=>r.tab);
export const extras=routes.filter(r=>!r.tab);
export const tabOf=(r:Route):Route=>tabs.some(t=>t.id===r)?r:'more';
export const fromHash=():Route=>(routes.find(r=>'#'+r.id===location.hash)?.id??'overview');
