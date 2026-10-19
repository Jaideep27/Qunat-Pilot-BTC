const {test}=require('node:test');
const assert=require('node:assert/strict');
const {classify,check}=require('../scripts/service_readiness.cjs');
test('a responding old API is explicitly marked for reload',()=>{
  const result=classify(200,404,{});assert.equal(result.ready,false);assert.equal(result.status,'reload_required');
});
test('healthy HTTP alone cannot mark an unavailable monitor ready',()=>{
  assert.equal(classify(200,503,{}).status,'monitor_unavailable');
  assert.equal(classify(503,200,{data:{ready:true}}).status,'api_unavailable');
});
test('failed readiness gates are preserved for the owner',()=>{
  const result=classify(200,200,{data:{ready:false,checks:{run_monitoring:{ok:false,status:'not_started'},database:{ok:true,status:'connected'}}}});
  assert.deepEqual(result.failed_checks,[{name:'run_monitoring',status:'not_started'}]);assert.equal(result.ready,false);
});
test('a valid fully ready response passes',()=>{
  assert.equal(classify(200,200,{data:{ready:true,checks:{}}}).ready,true);
});
test('malformed monitoring cannot claim readiness',()=>{
  assert.equal(classify(200,200,{data:{ready:'true'}}).status,'invalid_monitor_response');
});
test('request failures remain unavailable without publishing raw errors',async()=>{
  const result=await check(async()=>{throw new Error('private credential')});
  assert.equal(result.ready,false);assert.equal(result.status,'api_unreachable');assert.ok(!JSON.stringify(result).includes('private'));
});
test('the checker only reads health and bounded monitoring endpoints',async()=>{
  const requests=[];
  const result=await check(async url=>{requests.push(url);return {status:200,json:async()=>({data:{ready:true,checks:{}}})}});
  assert.equal(result.ready,true);assert.equal(requests.length,2);assert.ok(requests[1].endsWith('/operations?limit=1'));
});
