// Static repository integrity checks. This is not a compiler or functional test runner.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const read=p=>fs.readFileSync(p,'utf8').replace(/^\uFEFF/,'');
const walk=(dir,ext)=>fs.readdirSync(dir,{withFileTypes:true}).flatMap(e=>e.isDirectory()?(e.name==='__pycache__'?[]:walk(dir+'/'+e.name,ext)):(e.name.endsWith(ext)?[dir+'/'+e.name]:[]));
const checks=[];
function check(name,fn){fn();checks.push(name)}
check('Bootstrap configuration uses UTF-8 without BOM',()=>{
 for(const p of ['pyproject.toml','.python-version','.env.example','frontend/package.json']){
  const bytes=fs.readFileSync(p);
  assert.notEqual(bytes.subarray(0,3).toString('hex'),'efbbbf',p+': unexpected BOM');
 }
});
check('JSON configuration syntax',()=>{
 for(const p of ['frontend/package.json','frontend/tsconfig.json','frontend/vercel.json','docs/evidence/coverage-report.json','docs/evidence/expected-archives.json'])JSON.parse(read(p));
});
check('Seventeen unique MCP tools, shared services and loopback-only writes',()=>{
 const mcp=read('src/quantpilot/api/mcp_server.py');
 const tools=[...mcp.matchAll(/@mcp\.tool\(structured_output=True\)\s+(?:async )?def (\w+)/g)].map(m=>m[1]);
 assert.equal(tools.length,17);assert.equal(new Set(tools).size,17);
 const functions=new Set([...read('src/quantpilot/api/services.py').matchAll(/^def (\w+)\(/gm)].map(m=>m[1]));
 for(const m of [...mcp.matchAll(/services\.(\w+)\(/g)])assert(functions.has(m[1]),m[1]);
 const api=read('src/quantpilot/api/app.py');
 for(const m of [...api.matchAll(/services\.(\w+)\(/g)])assert(functions.has(m[1]),m[1]);
 assert(!/@app\.(put|patch|delete)\(/.test(api));
 // Every write route checks the client is this machine before doing anything.
 for(const m of [...api.matchAll(/@app\.post\('([^']+)'\)\n(?:async )?def \w+\([^)]*\):\n((?:    .*\n){1,3})/g)])
  assert(m[2].includes('local_only(request)'),m[1]+' must be loopback-only');
 assert.equal([...api.matchAll(/@app\.post\(/g)].length,[...api.matchAll(/local_only\(request\)/g)].length);
});
check('Manifest uniqueness, UTC boundaries and warm-up',()=>{
 const report=JSON.parse(read('docs/evidence/coverage-report.json'));
 const m=JSON.parse(read('docs/evidence/expected-archives.json'));
 assert.equal(m.archives.length,874);
 assert.equal(new Set(m.archives.map(x=>x.filename)).size,874);
 assert.equal(report.expected_5m_windows,221760);
 assert.equal(+new Date(m.start)-+new Date(m.warmup_start),40*86400000);
 assert.equal(typeof report.release_ready,"boolean");
 assert(Number.isInteger(report.verified_archives)&&report.verified_archives>=0&&report.verified_archives<=874);
 assert(m.archives.every(x=>x.url.startsWith('https://data.binance.vision/data/futures/um/')));
});
check('Python imports are absolute and every quantpilot module they name exists',()=>{
 const exists=dotted=>{const p='src/'+dotted.replace(/\./g,'/');return fs.existsSync(p+'.py')||fs.existsSync(p+'/__init__.py')};
 for(const file of [...walk('src/quantpilot','.py'),...walk('tests','.py'),...walk('scripts','.py')]){
  const text=read(file);
  assert(!/^\s*from \.+\w* import/m.test(text),file+': relative import');
  for(const m of text.matchAll(/^\s*from (quantpilot(?:\.\w+)*) import/gm))assert(exists(m[1]),file+': '+m[1]);
  for(const m of text.matchAll(/^\s*import (quantpilot(?:\.\w+)+)/gm))assert(exists(m[1]),file+': '+m[1]);
 }
});
check('Local Markdown document links resolve',()=>{
 const files=['README.md',...walk('docs','.md')];
 for(const f of files)for(const m of [...read(f).matchAll(/\]\(([^)]+)\)/g)]){
  const target=m[1];
  if(!target.includes('://')&&!target.startsWith('#'))assert(fs.existsSync(path.resolve(path.dirname(f),target.split('#')[0])),f+': '+target);
 }
});
check('Eight dashboard views, attribution and browser audit source exist',()=>{
 const routes=read('frontend/src/app/routes.ts');
 for(const tab of ['Overview','Chart','Ask','Research','Rules','Paper','News','System'])assert(routes.includes("label:'"+tab+"'"),tab);
 assert(read('frontend/src/main.tsx').includes('https://www.tradingview.com/'));
 assert(read('frontend/tests/dashboard.spec.ts').includes('Execution ledger'));
 // The legacy cloud paper worker runs only on demand: a schedule would fail every 15 minutes without its database.
 assert(!/^\s*schedule:/m.test(read('.github/workflows/paper.yml')));
});
const sourceFiles=walk('src/quantpilot','.py');
const result={kind:'static_structure_only',passed:checks,python_files:sourceFiles.length,
 python_test_functions:walk('tests','.py').reduce((n,p)=>n+[...read(p).matchAll(/^def test_/gm)].length,0),
 source_sha256:Object.fromEntries(sourceFiles.map(p=>[p,crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex')])),
 functional_tests:'Not evaluated by static checks; see .verification/result.json and pytest.xml'};
fs.writeFileSync('docs/evidence/static-checks.json',JSON.stringify(result,null,2));
console.log(JSON.stringify({passed:checks.length,checks,python_files:result.python_files,python_test_functions:result.python_test_functions,functional_tests:result.functional_tests},null,2));

