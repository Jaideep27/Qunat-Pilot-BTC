import MacroCalendar from '../MacroCalendar';
import NewsPanel from '../NewsPanel';
import {PageHead} from '../ui';

export default function News(){
  return <>
    <PageHead title="News & macro" description="Source-linked news and official CPI, jobs and FOMC releases, timestamped when first observed. Context only: news never moves a number."/>
    <div className="stack"><MacroCalendar/><NewsPanel/></div>
  </>;
}
