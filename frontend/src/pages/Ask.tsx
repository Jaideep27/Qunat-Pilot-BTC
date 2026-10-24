import Chat from '../components/Chat';
import {PageHead} from '../components/ui';

export default function Ask({seed}:{seed:{text:string;id:number}|null}){
  return <>
    <PageHead title="Ask QuantPilot" description="Plain-language questions answered with validated numbers. Every answer is scored once its horizon passes."/>
    <Chat seed={seed}/>
  </>;
}
