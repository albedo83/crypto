// Dependency-free DOM smoke test: node tests/test_experiments_dashboard.js
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class Element {
  constructor(){this.children=[];this.style={};this.textContent='';this.clientWidth=600;}
  appendChild(child){this.children.push(child);}
  replaceChildren(){this.children=[];this.textContent='';}
  getContext(){return new Proxy({}, {get:()=>()=>{},set:()=>true});}
  set innerHTML(_){throw Error('External data must never use innerHTML');}
}
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const html=fs.readFileSync('alfred/web/static/master.html','utf8');
const source='// Experiments:'+html.split('// Experiments:')[1].split('// ── refresh ──')[0];
const context=vm.createContext({$,document:{createElement:()=>new Element()},console,Date,Number,Math,Array,String,AbortController,setTimeout,clearTimeout,fetch:async()=>{throw Error('offline');}});
vm.runInContext(source,context);
vm.runInContext(`renderExperiments({enabled:true,status:'running',books:[{label:'<img src=x onerror=alert(1)>',status:'running',equity:null,pnl:0,series:[[1,500],[2,501]]}],references:[],warnings:['<script>bad</script>']})`,context);
assert.equal($('researchBooks').children[0].children[0].textContent,'<img src=x onerror=alert(1)>');
assert.equal($('researchBooks').children[0].children[2].textContent,'—');
assert.equal($('researchBooks').children[0].children[3].textContent,'0.00');
assert.equal($('researchWarnings').textContent,'<script>bad</script>');
(async()=>{
  await vm.runInContext('refreshExperiments()',context);
  assert.equal($('researchBooks').children.length,0);
  assert.equal($('researchRefs').children.length,0);
  assert.match($('researchStatus').textContent,/Données indisponibles/);
  context.fetch=async()=>({ok:true,json:async()=>({enabled:false})});
  await vm.runInContext('refreshExperiments()',context);
  assert.equal($('researchStatus').textContent,'Expériences désactivées.');
  console.log('Dashboard smoke: null/zero, untrusted text, curves, failed refresh clearing, retry OK');
})().catch(e=>{console.error(e);process.exitCode=1;});
