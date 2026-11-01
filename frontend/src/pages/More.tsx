import {ChevronRight} from 'lucide-react';
import {extras,type Route} from '../app/routes';
import {PageHead} from '../components/ui';

export default function More({go}:{go:(r:Route)=>void}){
  return <div className="page-narrow">
    <PageHead title="More" description="Everything else, one tap away."/>
    <ul className="more-list" aria-label="More pages">{extras.map(r=>{const Icon=r.icon;return <li key={r.id}>
      <button type="button" onClick={()=>go(r.id)}><Icon size={20} aria-hidden/>
        <span><strong>{r.label}</strong><small className="muted">{r.about}</small></span><ChevronRight size={18} aria-hidden/></button></li>})}</ul>
  </div>;
}
