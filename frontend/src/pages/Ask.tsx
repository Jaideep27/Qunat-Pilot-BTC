import Chat from '../Chat';
import {PageHead} from '../ui';

export default function Ask({seed}:{seed:{text:string;id:number}|null}){
  return <>
    <PageHead title="Ask QuantPilot" description="Plain-language questions answered with validated numbers. Every answer is scored once its horizon passes."/>
    <Chat seed={seed}/>
  </>;
}
