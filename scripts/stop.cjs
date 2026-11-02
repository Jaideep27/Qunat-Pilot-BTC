// Stop every QuantPilot service on this laptop: background services, API, public gateway, dashboard and PostgreSQL.
// It first leaves a "stopped on purpose" note (data/ops/stopped.json) so the watchdog does not restart anything and
// the nightly backup does not send an alarm. `node scripts/start.cjs` removes the note and starts everything again.
// `--dry-run` lists what would be stopped and changes nothing. No secrets are printed.
const fs=require('node:fs');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');process.chdir(root);
const python=path.join(root,'.venv','Scripts','python.exe');
const pgCtl='C:/Program Files/PostgreSQL/18/bin/pg_ctl.exe';
const cluster=path.join(root,'data','postgres-local','cluster');
const dry=process.argv.includes('--dry-run');
if(process.argv.slice(2).some(a=>a!=='--dry-run'))throw new Error('Unknown option. Use node scripts/stop.cjs [--dry-run].');

function run(command,args){
  const r=spawnSync(command,args,{cwd:root,windowsHide:true,encoding:'utf8'});
  return {ok:r.status===0,out:((r.stdout||'')+(r.stderr||'')).trim()};
}

// This project's own processes only: the command line must name this folder (a sibling checkout does not match).
function projectProcesses(){
  const script="Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($env:QP_ROOT) } | "+
    "ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId,$_.Name,$_.CommandLine }";
  const r=spawnSync('powershell.exe',['-NoProfile','-Command',script],{windowsHide:true,encoding:'utf8',
    env:{...process.env,QP_ROOT:root+path.sep}});
  return (r.stdout||'').split(/\r?\n/).filter(Boolean).map(line=>{const [pid,name,...cmd]=line.split('|');
    return {pid:Number(pid),name,cmd:cmd.join('|')}}).filter(p=>p.pid!==process.pid);
}

const kinds=[['public gateway',p=>/serve_public\.py/.test(p.cmd)],
             ['dashboard',p=>p.name.toLowerCase()==='node.exe'&&/vite/.test(p.cmd)]];

const note=path.join(root,'data','ops','stopped.json');
if(dry)console.log('Dry run: nothing is stopped.');
else{fs.mkdirSync(path.dirname(note),{recursive:true});
  fs.writeFileSync(note,JSON.stringify({stopped_at:new Date().toISOString()})+'\n');
  console.log('Watchdog and nightly backup paused.')}

// The background services and the API: the existing helper checks each recorded process belongs to this folder.
for(const [label,args] of [['background services',[]],['API',['--api']]]){
  if(dry){console.log(`Would stop the ${label} (recorded process).`);continue}
  const r=run(python,['scripts/ops/stop_data_supervisor.py',...args]);
  console.log(r.ok?`Stopped the ${label}.`:`The ${label}: ${r.out||'not running'}`);
}
const found=projectProcesses();
for(const [label,match] of kinds){
  const hits=found.filter(match);
  if(!hits.length){console.log(`The ${label} is not running.`);continue}
  for(const p of hits){
    if(dry){console.log(`Would stop the ${label} (PID ${p.pid}).`);continue}
    const r=run('taskkill',['/PID',String(p.pid),'/T','/F']);
    console.log(r.ok?`Stopped the ${label} (PID ${p.pid}).`:`The ${label} (PID ${p.pid}) had already stopped.`);
  }
}
if(!fs.existsSync(path.join(cluster,'postmaster.pid')))console.log('The database is not running.');
else if(dry)console.log('Would stop the database (pg_ctl stop, fast and clean).');
else{const r=run(pgCtl,['stop','-D',cluster,'-m','fast','-w']);console.log(r.ok?'Stopped the database.':'The database: '+r.out)}
if(!dry)console.log('Everything is stopped. Start it again with: node scripts/start.cjs');
