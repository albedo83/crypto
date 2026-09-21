const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('alfred/web/static/reversal.html','utf8');
const source='function refreshSystemAudit(){'+html.split('function refreshSystemAudit(){')[1].split('function refreshBTDivergence(){')[0];
const elements={};const get=id=>elements[id]??=( {style:{},innerHTML:''} );
const context=vm.createContext({document:{getElementById:get},Date,String,console,
 fetch:async()=>({ok:true,json:async()=>({ts:1789983015,anomalies:[{titre:'Ancienne alerte',constat:'ancien constat'}],current_review_health:{status:'OK',message:'<script>unsafe</script>'}})})});
vm.runInContext(source,context);vm.runInContext('refreshSystemAudit()',context);
setTimeout(()=>{
 assert.match(get('system-audit').innerHTML,/Rapport du/);
 assert.match(get('system-audit').innerHTML,/contrôle actuel/);
 assert.match(get('system-audit').innerHTML,/Ancienne alerte/);
 assert.match(get('system-audit').innerHTML,/&lt;script&gt;/);
 assert.doesNotMatch(get('system-audit').innerHTML,/<script>/);
 console.log('Audit dashboard: historical report, current health and escaping OK');
},10);
