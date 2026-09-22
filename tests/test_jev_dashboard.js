const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('alfred/web/static/master.html','utf8');
const script=html.split('<script>').pop().split('</script>')[0];
const helpers=script.split('\n').filter(l=>/^const (\$|fmt|p2|fmtLoc|esc)=/.test(l)).join('\n');
const i=script.indexOf('function renderAI(');assert.ok(i>=0);
let d=0,j=script.indexOf('{',i);
for(;j<script.length;j++){if(script[j]==='{')d++;else if(script[j]==='}'&&--d===0)break;}
const source=helpers+'\n'+script.slice(i,j+1);
const elements={};const get=id=>elements[id]??=({style:{},innerHTML:''});
const ctx=vm.createContext({document:{getElementById:get},Date,String,Number,Math,JSON,Object,console});
vm.runInContext(source,ctx);
ctx.data={jev:{mode:'shadow',scorecard:{questions_hash:'abc',failopen:1,
 entry:{n:3,resolved:2,pending:1,not_entered:0,GO:{n:1,wins:1,pnl:20},HOLD:{n:1,wins:0,pnl:-10},delta_if_hold_vetoed:10,brier:0.04,brier_base:0.25},
 exit:{n:2,positions:1,resolved:1,cut_positions:1,delta_if_cut:6,brier:null}},
 recent:[{ts:1790000000,event:'JEV_ENTRY_SHADOW',symbol:'<b>X',data:{decision:'HOLD',win:0.2,strategy:'S5',dir:'SHORT'}},
  {ts:1790000000,event:'JEV_FAILOPEN',symbol:null,data:{phase:'exit',errors:{A:'timeout'}}}]}};
vm.runInContext('renderAI(data)',ctx);
const h=get('aiJev').innerHTML;
assert.match(h,/JEV/);assert.match(h,/Live · shadow/);assert.match(h,/aucun effet sur les ordres/);
assert.match(h,/Δ si HOLD=veto/);assert.match(h,/Δ si CUT agi/);assert.match(h,/échec/);
assert.match(h,/&lt;b&gt;X/);assert.doesNotMatch(h,/<b>X/);
vm.runInContext('renderAI({})',ctx);assert.equal(get('aiJev').innerHTML,'');
console.log('JEV dashboard: scorecard, recent verdicts, failures and escaping OK');
