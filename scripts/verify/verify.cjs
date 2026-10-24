// Portable verification. Run: node scripts/verify/verify.cjs
// Uses ordinary uv/npm commands and records evidence; changes no system execution policy.
const fs=require('node:fs');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const options=process.argv.slice(2);
if(options.includes('--help')){
 console.log('Usage: node scripts/verify/verify.cjs [--frontend]\n--frontend: run static checks, frontend install, build and browser tests only.');
 process.exit(0);
}
if(options.some(option=>option!=='--frontend')){
 console.error('Unknown option. Use --help for usage.');
 process.exit(2);
}
const frontendOnly=options.includes('--frontend');
const root=path.resolve(__dirname,'../..');
const evidence=path.join(root,'.verification');
fs.mkdirSync(evidence,{recursive:true});
const env={...process.env,UV_CACHE_DIR:path.join(root,'.uv-cache'),
 UV_PYTHON_INSTALL_DIR:path.join(root,'.python'),npm_config_cache:path.join(root,'.npm-cache')};
const npm=process.platform==='win32'?'npm.cmd':'npm';
const npx=process.platform==='win32'?'npx.cmd':'npx';
const allSteps=[
 {name:'Static repository integrity',command:process.execPath,args:['scripts/verify/check-structure.cjs']},
 {name:'Local service readiness checks',command:process.execPath,args:['--test','tests/service-readiness.test.cjs']},
 {name:'Python dependencies',command:'uv',args:['sync','--locked','--extra','dev']},
 {name:'ML dependency imports',command:'uv',args:['run','python','-c','import numpy, numba, shap, xgboost, sklearn; print({m.__name__: m.__version__ for m in (numpy, numba, shap, xgboost, sklearn)})']},
 {name:'Python compilation',command:'uv',args:['run','python','-m','compileall','-q','src','tests','migrations','scripts']},
 {name:'Python regression suite',command:'uv',args:['run','pytest','-q','--junitxml=.verification/pytest.xml']},
 {name:'Frontend dependencies',command:npm,args:['ci'],cwd:'frontend'},
 {name:'TypeScript and Vite build',command:npm,args:['run','build'],cwd:'frontend'},
 {name:'Chromium installation',command:npx,args:['playwright','install','chromium'],cwd:'frontend'},
 {name:'Browser regression suite',command:npm,args:['test'],cwd:'frontend'}
];
const steps=frontendOnly?allSteps.filter(step=>step.cwd==='frontend'||step.name==='Static repository integrity'):allSteps;
const resultFile=frontendOnly?'result-frontend.json':'result.json';
const summary={scope:frontendOnly?'frontend':'full',passed:false,started_at:new Date().toISOString(),
 commands:steps.map(s=>({name:s.name,status:'not_run',exit_code:null}))};
try{
 for(let i=0;i<steps.length;i++){
  const step=steps[i],record=summary.commands[i];
  console.log('\n'+step.name);
  const result=spawnSync(step.command,step.args,{cwd:step.cwd?path.join(root,step.cwd):root,env,
   stdio:'inherit',windowsHide:true,shell:process.platform==='win32'&&step.command.endsWith('.cmd')});
  record.exit_code=result.status;
  record.status=result.status===0&&!result.error?'passed':'failed';
  if(result.error)record.error=result.error.code||result.error.message;
  if(record.status==='failed'){
   summary.error=step.name+' failed'+(record.error?': '+record.error:'');
   process.exitCode=1;
   break;
  }
 }
 summary.passed=summary.commands.every(s=>s.status==='passed');
}catch(error){
 summary.error=error.message;
 process.exitCode=1;
}finally{
 summary.finished_at=new Date().toISOString();
 fs.writeFileSync(path.join(evidence,resultFile),JSON.stringify(summary,null,2)+'\n','utf8');
 console.log('\nEvidence: '+path.join(evidence,resultFile));
}

