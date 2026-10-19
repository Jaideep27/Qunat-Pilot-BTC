const fs=require('node:fs'),path=require('node:path');
const roots=['src','tests','frontend/src','frontend/tests','migrations','scripts','docs','.github'];
const rootFiles=['.python-version','.env.example','.gitignore','.dockerignore','pyproject.toml','alembic.ini','Dockerfile','compose.yaml','render.yaml','README.md','THIRD_PARTY_NOTICES.md','frontend/package.json','frontend/tsconfig.json','frontend/index.html','frontend/vercel.json','frontend/vite.config.ts','frontend/playwright.config.ts'];
let count=0;
function normalize(p){
 const data=fs.readFileSync(p);
 if(data.length>=3&&data[0]===0xef&&data[1]===0xbb&&data[2]===0xbf){
  fs.writeFileSync(p,data.subarray(3));count++;
 }
}
function walk(p){
 for(const e of fs.readdirSync(p,{withFileTypes:true})){
  const target=path.join(p,e.name);
  if(e.isDirectory())walk(target);
  else if(/\.(py|ts|tsx|css|html|md|json|toml|yml|yaml|ps1|cjs)$/.test(e.name))normalize(target);
 }
}
for(const p of roots)walk(p);
for(const p of rootFiles)if(fs.existsSync(p))normalize(p);
console.log('Normalized '+count+' authored text files to UTF-8 without BOM.');

