// Inspect the running API without starting, stopping or mutating any service.
function classify(healthStatus,operationsStatus,payload){
  if(healthStatus!==200)return {ready:false,status:'api_unavailable',message:'API health is unavailable.'};
  if(operationsStatus===404)return {ready:false,status:'reload_required',message:'The running API has not loaded monitoring. Reload with node scripts/start.cjs --restart-services from the regular terminal.'};
  if(operationsStatus!==200)return {ready:false,status:'monitor_unavailable',message:'Operational monitoring is unavailable; inspect database connectivity and API logs.'};
  if(typeof payload?.data?.ready!=='boolean')return {ready:false,status:'invalid_monitor_response',message:'The API returned an invalid operational response.'};
  const failed=Object.entries(payload.data.checks??{}).filter(([,check])=>!check.ok).map(([name,check])=>({name,status:check.status}));
  return {ready:payload.data.ready,status:payload.data.ready?'ready':'needs_attention',
    message:payload.data.ready?'Local operational readiness passed.':'Services are responding; operational readiness needs attention.',failed_checks:failed};
}
async function check(fetcher=fetch,base='http://127.0.0.1:8000'){
  try{
    const [health,operations]=await Promise.all([
      fetcher(base+'/api/v1/health',{signal:AbortSignal.timeout(10000)}),
      fetcher(base+'/api/v1/operations?limit=1',{signal:AbortSignal.timeout(10000)})]);
    let payload;try{payload=await operations.json()}catch{}
    return classify(health.status,operations.status,payload);
  }catch{return {ready:false,status:'api_unreachable',message:'API could not be reached within the local check timeout.'}}
}
module.exports={classify,check};
