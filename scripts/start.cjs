// Start the PostgreSQL-backed local application. No secrets are printed.
const fs=require('node:fs');
const path=require('node:path');
const {spawn,spawnSync}=require('node:child_process');
const net=require('node:net');
const crypto=require('node:crypto');
const root=path.resolve(__dirname,'..');process.chdir(root);
const logs=path.join(root,'.verification','services');
const python=path.join(root,'.venv','Scripts','python.exe');
const portOpen=port=>new Promise(resolve=>{const socket=net.connect({host:'127.0.0.1',port});socket.setTimeout(1500);
  socket.on('connect',()=>{socket.destroy();resolve(true)});socket.on('error',()=>resolve(false));socket.on('timeout',()=>{socket.destroy();resolve(false)})});
function background(name,command,args){
  fs.mkdirSync(logs,{recursive:true});
  const out=fs.openSync(path.join(logs,name+'.log'),'a');
  const child=spawn(command,args,{cwd:root,windowsHide:true,detached:true,stdio:['ignore',out,out],env:process.env});
  child.unref();fs.closeSync(out);console.log(name+' started (PID '+child.pid+').');return child.pid;
}
function run(command,args){const result=spawnSync(command,args,{cwd:root,windowsHide:true,encoding:'utf8'});
  if(result.status!==0)throw new Error((result.stderr||result.stdout||'Command failed').replace(/postgresql[^\s]+/g,'[database URL hidden]'))}
(async()=>{
  const options=process.argv.slice(2);
  if(options.some(option=>!['--check','--restart-data','--restart-services'].includes(option)))throw new Error('Unknown option. Use --check, --restart-data or --restart-services.');
  if(options.includes('--check')){
    if(options.length!==1)throw new Error('--check cannot be combined with restart options.');
    const status=await require('./service_readiness.cjs').check();
    console.log(JSON.stringify(status,null,2));
    if(!status.ready)process.exitCode=2;
    return;
  }
  if(!fs.existsSync(python))throw new Error('Install dependencies with uv sync --locked --extra dev --extra rl first.');
  if(process.argv.includes('--restart-data')||process.argv.includes('--restart-services'))run(python,['scripts/stop_data_supervisor.py']);
  if(process.argv.includes('--restart-services'))run(python,['scripts/stop_data_supervisor.py','--api']);
  const pg='C:/Program Files/PostgreSQL/18/bin';
  if(!await portOpen(55432)){
    if(!fs.existsSync(path.join(root,'data/postgres-local/cluster/PG_VERSION'))){
      const dir=path.join(root,'data/postgres-local');fs.mkdirSync(dir,{recursive:true});
      const pw=path.join(dir,'bootstrap-password');if(!fs.existsSync(pw))fs.writeFileSync(pw,crypto.randomBytes(36).toString('base64'));
      run(path.join(pg,'initdb.exe'),['-D',path.join(dir,'cluster'),'-U','quantpilot','--pwfile='+pw,
        '--auth=scram-sha-256','--encoding=UTF8','--locale=C']);
    }
    background('postgres',path.join(pg,'postgres.exe'),['-D',path.join(root,'data/postgres-local/cluster'),'-p','55432','-h','127.0.0.1']);
    for(let attempt=0;attempt<20&&!await portOpen(55432);attempt++)await new Promise(resolve=>setTimeout(resolve,500));
    if(!await portOpen(55432))throw new Error('PostgreSQL did not start; inspect service logs.');
  }
  if(!fs.existsSync('.env'))run(python,['scripts/configure_postgres.py']);
  run(python,['-m','quantpilot.cli','init-db']);
  if(!await portOpen(8000))background('api',python,['scripts/serve_api.py']);
  if(!await portOpen(5173))background('dashboard',process.env.ComSpec||'cmd.exe',['/d','/s','/c','npm --prefix frontend run dev']);
  // The supervisor owns its lock, so running this launcher twice does not duplicate jobs.
  background('data-supervisor',python,['scripts/data_supervisor.py']);
  let ready=false;
  for(let attempt=0;attempt<90;attempt++){
    try{const response=await fetch('http://127.0.0.1:8000/api/v1/health',{signal:AbortSignal.timeout(3000)});
      if(response.ok){ready=true;break}}catch{}
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
  if(!ready)throw new Error('API did not become ready; inspect .verification/services/api.log.');
  console.log('Workspace: http://localhost:5173/#chart');
  console.log('API: http://127.0.0.1:8000/docs');
  console.log('Continuous collection and resumable history run in the background. Actual status is shown in the workspace.');
  const status=await require('./service_readiness.cjs').check();
  console.log(status.message);
  if(status.failed_checks?.length)console.log('Readiness gates: '+status.failed_checks.map(check=>check.name+' ('+check.status+')').join(', '));
  if(!status.ready)process.exitCode=2;
})().catch(error=>{console.error(error.message);process.exitCode=1});
