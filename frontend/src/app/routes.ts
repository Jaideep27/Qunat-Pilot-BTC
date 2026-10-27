import {BookOpen,CandlestickChart,FlaskConical,LayoutDashboard,ListChecks,MessageSquare,ServerCog,Wallet} from 'lucide-react';
import {publicView} from '../lib/api';

const all=[
  {id:'overview',label:'Overview',icon:LayoutDashboard},
  {id:'chart',label:'Chart',icon:CandlestickChart},
  {id:'ask',label:'Ask',icon:MessageSquare},
  {id:'research',label:'Research',icon:FlaskConical},
  {id:'rules',label:'Rules',icon:ListChecks},
  {id:'paper',label:'Paper',icon:Wallet},
  {id:'news',label:'News',icon:BookOpen},
  {id:'system',label:'System',icon:ServerCog},
] as const;
export type Route=typeof all[number]['id'];
// Ask runs on the owner's Claude subscription and shows their chat history, so the public view leaves it out.
export const routes=publicView?all.filter(r=>r.id!=='ask'):all;
export const fromHash=():Route=>(routes.find(r=>'#'+r.id===location.hash)?.id??'overview');
