import {Archive,BookOpen,Bot,CandlestickChart,FlaskConical,Gauge,House,LayoutGrid,ListChecks,MessageSquare,ServerCog,Wallet,Workflow} from 'lucide-react';
import {publicView} from '../lib/api';

// The analysis app (A15): five tabs. Everything else lives under More (and stays reachable by link and Ctrl+K).
// `hidden` pages are archived trading pages: reachable from "Trading history", not listed under More.
const all=[
  {id:'overview',label:'Home',icon:House,tab:true,hidden:false,about:''},
  {id:'indicators',label:'Indicators',icon:Gauge,tab:true,hidden:false,about:''},
  {id:'strategies',label:'Strategies',icon:Workflow,tab:true,hidden:false,about:''},
  {id:'chart',label:'Chart',icon:CandlestickChart,tab:true,hidden:false,about:''},
  {id:'more',label:'More',icon:LayoutGrid,tab:true,hidden:false,about:''},
  {id:'news',label:'News',icon:BookOpen,tab:false,hidden:false,about:'Headlines, AI news analysis and the economic calendar.'},
  {id:'ask',label:'Ask',icon:MessageSquare,tab:false,hidden:false,about:'Ask a question about BTC in plain words.'},
  {id:'research',label:'Research',icon:FlaskConical,tab:false,hidden:false,about:'What was tested, and what can and cannot be predicted.'},
  {id:'archive',label:'Trading history',icon:Archive,tab:false,hidden:false,about:'Archived: the paper accounts and their records.'},
  {id:'system',label:'System',icon:ServerCog,tab:false,hidden:false,about:'Data feeds, services and backups.'},
  {id:'rules',label:'Rule engine',icon:ListChecks,tab:false,hidden:true,about:'Archived: the rule engine journal.'},
  {id:'paper',label:'Old paper account',icon:Wallet,tab:false,hidden:true,about:'Archived: the first forward paper account.'},
  {id:'bots',label:'Bots',icon:Bot,tab:false,hidden:true,about:'Archived: the bot cards.'},
] as const;
export type Route=typeof all[number]['id'];
// Ask runs on the owner's Claude subscription and shows their chat history, so the public view leaves it out.
export const routes=publicView?all.filter(r=>r.id!=='ask'):all;
export const tabs=routes.filter(r=>r.tab);
export const extras=routes.filter(r=>!r.tab&&!r.hidden);
const archived=new Set<string>(routes.filter(r=>r.hidden).map(r=>r.id));
export const tabOf=(r:Route):Route=>tabs.some(t=>t.id===r)?r:'more';
export const isArchived=(r:Route)=>archived.has(r);
export const fromHash=():Route=>(routes.find(r=>'#'+r.id===location.hash)?.id??'overview');
