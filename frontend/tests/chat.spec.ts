import {test,expect} from '@playwright/test';

const answer={id:'a1',question:{type:'direction',horizon_hours:4},price:110000,stale:false,warnings:[],
  headline:{kind:'probability',probability:.51,base_rate:.51,method:'base_rate',confidence:'no_edge'},
  range:{low:109300,high:110700,coverage:.8,method:'range_model'},evidence:{funding_rate:{value:.0001,percentile:92,label:'funding rate'}},
  events:[],news:[],ai:{status:'off'}};

test('chat shows replies, tools used, answer cards and quant-only quick questions',async({page})=>{
  const values:Record<string,unknown>={candles:[],indicators:{},positioning:[],sentiment:[],coverage:{release_ready:false},
    predictions:{action:'NO_TRADE',reasons:['WEAK_FORECAST']},threads:[],answers:{history:[],scoreboard:{}}};
  await page.route('**/api/v1/**',route=>{
    const url=new URL(route.request().url());const key=url.pathname.split('/').at(-1)!;
    if(url.pathname.endsWith('/chat')&&route.request().method()==='POST')
      return route.fulfill({json:{data:{thread_id:'t1',reply:{text:'About 51%, no edge: roughly a coin flip.',tools:['ask_outlook','get_macro_calendar'],answer_ids:['a1'],ai_status:'ok'}}}});
    if(url.pathname.endsWith('/chat/threads/t1'))
      return route.fulfill({json:{data:{id:'t1',title:'up or down next 4h',messages:[
        {role:'user',text:'up or down next 4h'},
        {role:'assistant',text:'About 51%, no edge: roughly a coin flip.',tools:['ask_outlook','get_macro_calendar'],answer_ids:['a1'],answers:[answer],ai_status:'ok'}]}}});
    if(url.pathname.endsWith('/ask')) return route.fulfill({json:{data:answer}});
    return route.fulfill({json:{data:values[key]??{},provenance:{source:'fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});
  });
  await page.goto('/');await page.getByRole('button',{name:'Chat',exact:true}).click();
  const chat=page.getByRole('region',{name:'QuantPilot chat'});
  await chat.getByRole('textbox',{name:'Message'}).fill('up or down next 4h');
  await chat.getByRole('button',{name:'Send'}).click();
  await expect(chat).toContainText('roughly a coin flip');
  await expect(chat).toContainText('ask_outlook');
  await expect(chat).toContainText('51.0%');
  await expect(chat).toContainText('No edge');
  await chat.getByRole('button',{name:'Up or down next 1h?'}).click();
  await expect(chat).toContainText('Quant-only answer');
});

const scored={...answer,id:'a2',asked_at:'2026-10-20T08:00:00+00:00',ai:{status:'ok',probability:.52,reasons:[]},
  track_record:{validation:{promoted:false,windows:2772},forward:{scored:12,independent:3,brier:.251,base_brier:.25,ai_brier:.249,verdict:'too early to judge'}},
  outcome:{hit:1}};
const pending={...scored,id:'a3',outcome:undefined};

async function openChat(page:import('@playwright/test').Page,chatReply:object){
  const values:Record<string,unknown>={candles:[],indicators:{},positioning:[],sentiment:[],coverage:{release_ready:false},
    predictions:{action:'NO_TRADE',reasons:['WEAK_FORECAST']},threads:[],
    answers:{history:[scored,pending],scoreboard:{'direction:4h':{scored:12,independent:3,brier:.251,base_brier:.25,ai_brier:.249,verdict:'too early to judge'}}}};
  await page.route('**/api/v1/**',route=>{
    const url=new URL(route.request().url());const key=url.pathname.split('/').at(-1)!;
    if(url.pathname.endsWith('/chat')&&route.request().method()==='POST')return route.fulfill({json:{data:{thread_id:'t2',reply:chatReply}}});
    if(url.pathname.endsWith('/chat/threads/t2'))return route.fulfill({json:{data:{id:'t2',title:'q',messages:[{role:'user',text:'q'},
      {role:'assistant',...chatReply,answers:(chatReply as {answer_ids:string[]}).answer_ids.length?[scored]:[]}]}}});
    return route.fulfill({json:{data:values[key]??{},provenance:{source:'fixture',freshness:'fresh',observed_at:null},model_version:null,config_version:'fixture'}});
  });
  await page.goto('/');await page.getByRole('button',{name:'Chat',exact:true}).click();
  const chat=page.getByRole('region',{name:'QuantPilot chat'});
  await chat.getByRole('textbox',{name:'Message'}).fill('q');await chat.getByRole('button',{name:'Send'}).click();
  return chat;
}

test('answer cards show evidence, the separately scored AI view and the track record; the scoreboard lists outcomes',async({page})=>{
  const chat=await openChat(page,{text:'About 51%.',tools:['ask_outlook'],answer_ids:['a2'],ai_status:'ok'});
  await expect(chat).toContainText('funding rate');
  await expect(chat).toContainText('92nd percentile');
  await expect(chat).toContainText("Claude's view 52.0% (scored separately)");
  await expect(chat).toContainText('12 scored');
  const board=page.getByRole('region',{name:'Answer scoreboard'});
  await expect(board).toContainText('direction:4h');
  await expect(board).toContainText('too early to judge');
  await expect(board).toContainText('✓');
  await expect(board).toContainText('pending');
});

test('an AI-unavailable reply says so',async({page})=>{
  const chat=await openChat(page,{text:"Claude's usage limit is reached for now.",tools:[],answer_ids:[],ai_status:'usage_limited'});
  await expect(chat).toContainText('usage limit is reached');
  await expect(chat).toContainText('AI unavailable: usage limited');
});

test('chat text renders its separators correctly (UTF-8 source)',async({page})=>{
  const chat=await openChat(page,{text:'ok',tools:[],answer_ids:[],ai_status:'ok'});
  await expect(chat).toContainText('CLAUDE · QUANTPILOT TOOLS ONLY · NEVER TRADES');
});
