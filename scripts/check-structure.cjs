// Static repository integrity checks. This is not a compiler or functional test runner.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const read=p=>fs.readFileSync(p,'utf8').replace(/^\uFEFF/,'');
const checks=[];
function check(name,fn){fn();checks.push(name)}
check('Bootstrap configuration uses UTF-8 without BOM',()=>{
 for(const p of ['pyproject.toml','.python-version','.env.example','frontend/package.json']){
  const bytes=fs.readFileSync(p);
  assert.notEqual(bytes.subarray(0,3).toString('hex'),'efbbbf',p+': unexpected BOM');
 }
});
check('JSON configuration syntax',()=>{
 for(const p of ['frontend/package.json','frontend/tsconfig.json','frontend/vercel.json','docs/coverage-report.json','docs/expected-archives.json'])JSON.parse(read(p));
});
check('Nine unique read-only MCP tools and shared services',()=>{
 const mcp=read('src/quantpilot/mcp_server.py');
 const tools=[...mcp.matchAll(/@mcp\.tool\(structured_output=True\)\s+(?:async )?def (\w+)/g)].map(m=>m[1]);
 assert.equal(tools.length,9);assert.equal(new Set(tools).size,9);
 const functions=new Set([...read('src/quantpilot/services.py').matchAll(/^def (\w+)\(/gm)].map(m=>m[1]));
 for(const m of [...mcp.matchAll(/services\.(\w+)\(/g)])assert(functions.has(m[1]),m[1]);
 for(const m of [...read('src/quantpilot/api.py').matchAll(/services\.(\w+)\(/g)])assert(functions.has(m[1]),m[1]);
 assert(!/@app\.(post|put|patch|delete)\(/.test(read('src/quantpilot/api.py')));
});
check('Manifest uniqueness, UTC boundaries and warm-up',()=>{
 const report=JSON.parse(read('docs/coverage-report.json'));
 const m=JSON.parse(read('docs/expected-archives.json'));
 assert.equal(m.archives.length,874);
 assert.equal(new Set(m.archives.map(x=>x.filename)).size,874);
 assert.equal(report.expected_5m_windows,221760);
 assert.equal(+new Date(m.start)-+new Date(m.warmup_start),40*86400000);
 assert.equal(typeof report.release_ready,"boolean");
 assert(Number.isInteger(report.verified_archives)&&report.verified_archives>=0&&report.verified_archives<=874);
 assert(m.archives.every(x=>x.url.startsWith('https://data.binance.vision/data/futures/um/')));
});
check('Python relative module imports resolve',()=>{
 for(const name of fs.readdirSync('src/quantpilot').filter(x=>x.endsWith('.py'))){
  for(const m of [...read('src/quantpilot/'+name).matchAll(/from \.(\w+) import/g)]){
   assert(fs.existsSync('src/quantpilot/'+m[1]+'.py'),name+': '+m[1]);
  }
 }
});
check('Local Markdown document links resolve',()=>{
 const files=['README.md',...fs.readdirSync('docs').filter(p=>p.endsWith('.md')).map(p=>'docs/'+p)];
 for(const f of files)for(const m of [...read(f).matchAll(/\]\(([^)]+)\)/g)]){
  const target=m[1];
  if(!target.includes('://')&&!target.startsWith('#'))assert(fs.existsSync(path.resolve(path.dirname(f),target.split('#')[0])),f+': '+target);
 }
});
check('Four dashboard views, attribution and browser audit source exist',()=>{
 const ui=read('frontend/src/main.tsx');
 for(const tab of ['Market','Prediction','Research','Paper portfolio'])assert(ui.includes(tab));
 assert(ui.includes('https://www.tradingview.com/'));
 assert(read('frontend/tests/dashboard.spec.ts').includes('Execution ledger'));
 assert(read('.github/workflows/paper.yml').includes('2,17,32,47 * * * *'));
});
const sourceFiles=fs.readdirSync('src/quantpilot').filter(x=>x.endsWith('.py')).map(x=>'src/quantpilot/'+x);
const result={kind:'static_structure_only',passed:checks,python_files:sourceFiles.length,
 python_test_functions:fs.readdirSync('tests').filter(x=>x.endsWith('.py')).reduce((n,p)=>n+[...read('tests/'+p).matchAll(/^def test_/gm)].length,0),
 source_sha256:Object.fromEntries(sourceFiles.map(p=>[p,crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex')])),
 functional_tests:'Not evaluated by static checks; see .verification/result.json and pytest.xml'};
fs.writeFileSync('docs/static-checks.json',JSON.stringify(result,null,2));
console.log(JSON.stringify({passed:checks.length,checks,python_files:result.python_files,python_test_functions:result.python_test_functions,functional_tests:result.functional_tests},null,2));

