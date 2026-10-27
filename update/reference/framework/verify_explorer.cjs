// Unit checks of local HTML rendering/selection functions. No browser or user UI access.
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert');
const root=__dirname,html=fs.readFileSync(path.join(root,'노션_공유용','위협지도_탐색.html'),'utf8');
const jsonText=html.match(/<script type="application\/json" id="explorer-data">([\s\S]*?)<\/script>/)[1];const D=JSON.parse(jsonText),script=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1];
const elements=new Map();class Element{constructor(id=''){this.id=id;this.attrs={};this.value='all';this.hidden=false;this.style={};this.listeners={};this.classList={add(){},toggle(){}};this._html='';this.textContent=''}set innerHTML(s){this._html=s;for(const m of s.matchAll(/\bid="([^"]+)"/g))if(!elements.has(m[1]))elements.set(m[1],new Element(m[1]))}get innerHTML(){return this._html}setAttribute(k,v){this.attrs[k]=String(v)}getAttribute(k){return this.attrs[k]}querySelector(){return null}querySelectorAll(){return []}addEventListener(k,f){this.listeners[k]=f}insertAdjacentHTML(_,s){this.innerHTML=this.innerHTML+s}click(){this.listeners.click?.({target:this})}scrollIntoView(){}showModal(){}close(){}}
for(const m of html.matchAll(/\bid="([^"]+)"/g))elements.set(m[1],new Element(m[1]));elements.get('explorer-data').textContent=jsonText;
const document={getElementById:id=>elements.get(id)??null,addEventListener(){},createElementNS(){return new Element()}};
const context=vm.createContext({document,history:{replaceState(){}},location:{hash:''},window:{addEventListener(){}},innerWidth:1440,matchMedia:()=>({matches:true}),console});vm.runInContext(script,context,{timeout:5000});
const run=code=>vm.runInContext(code,context,{timeout:5000});
assert.equal(run('D.groups.length'),24);assert.equal(run('D.scenarios.length'),83);assert.equal(run('D.checks.length'),77);assert.equal(run('D.environment_axes.length'),12);
assert.equal(elements.get('filter-summary').textContent,'24개 묶음 · 83개 상황 · 77개 점검');
let scs=0,checks=0,branches=0,variants=0;
for(const s of D.scenarios){
 const id=JSON.stringify(s.id);const rendered=run(`renderScenario(S.get(${id}))`);
 for(const key of ['condition','violation'])assert(rendered.includes(run(`txt(S.get(${id})[${JSON.stringify(key)}])`)),s.id+': raw '+key);
 assert(rendered.includes(D.scenario_reader[s.id].title)||D.scenario_reader[s.id].explain, 'guide exists');
 const proof=run(`renderEvidence(S.get(${id}))`);
 for(const v of s.review_variants??[]){for(const key of ['sources','normal','fail','unknown'])for(const text of (Array.isArray(v[key])?v[key]:[v[key]])){if(text)assert(proof.includes(run(`txt(${JSON.stringify(text)})`)),`${s.id}/${v.id}/${key}`)}variants++}
 run(`setScenario(${id})`);assert(elements.get('detail').innerHTML.includes(D.scenario_reader[s.id].title));scs++;
}
for(const c of D.checks){const sid=JSON.stringify(c.scenario_ids[0]),cid=JSON.stringify(c.id);run(`state.scenario=${sid};state.group=S.get(${sid}).group;state.check=${cid};state.detailTab='checks'`);const output=run(`renderChecks(S.get(${sid}))`);
 for(const key of ['satisfied_if','fail_criteria','unknown_criteria','normal_control','remaining_setup'])assert(output.includes(run(`txt(C.get(${cid})[${JSON.stringify(key)}])`)),c.id+': '+key);
 for(const key of ['challenge_steps','required_observations'])for(const text of c[key])assert(output.includes(run(`txt(${JSON.stringify(text)})`)),c.id+': '+key);
 for(const b of c.test_branch_conditions){assert(output.includes(run(`txt(${JSON.stringify(b.apply_when)})`)),c.id+': branch');branches++}checks++;
}
run("Object.assign(state,{area:'all',axis:'all',scope:'all',kind:'all',query:'',node:null});render()");
for(const node of D.map_model.nodes.map(n=>n.id)){run(`state.node=${JSON.stringify(node)}`);assert.equal(run('currentScenarios().length'),D.scenarios.filter(s=>s.map_touchpoints.nodes.includes(node)).length,node+' filter/map agreement')}
run("state.node=null;state.axis='12';render()");assert.equal(run('currentScenarios().length'),0);assert(elements.get('axis-context').textContent.includes('검토 대기'));assert(elements.get('detail').innerHTML.includes('검토는 대기'));
run("state.axis='all';state.kind='failure';render()");assert.equal(run('currentScenarios().length'),9);run("state.kind='위협 후보';render()");assert.equal(run('currentScenarios().length'),73);run("state.kind='context';render()");assert.equal(run('currentScenarios().length'),1);
run("state.kind='all';state.query='CHK-SUP-005';render()");assert.equal(run('currentScenarios().length'),1);
run("state.query='';state.node='caller';render()");assert.equal(run('currentScenarios().length'),D.scenarios.filter(s=>s.map_touchpoints.nodes.includes('caller')).length);
assert.equal(run("typeof er('12').question"),'string');
// STRIDE classification, combined filtering and navigation checks.
run("Object.assign(state,{area:'all',axis:'all',scope:'all',kind:'all',type:'all',query:'',node:null});render()");
const canonical=JSON.parse(fs.readFileSync(path.join(root,'data','checklist.json'),'utf8'));
assert.deepStrictEqual(D.threat_classification_guide,canonical.threat_classification_guide);
for(const s of D.scenarios){
 assert.deepStrictEqual(s.threat_classification,canonical.scenarios.find(x=>x.id===s.id).threat_classification,s.id+' classification sync');
 const out=run(`renderScenario(S.get(${JSON.stringify(s.id)}))`);
 assert(out.includes(run(`txt(${JSON.stringify(s.threat_classification.reason)})`)),s.id+' reason visible');
 assert(out.includes(run(`txt(${JSON.stringify(s.threat_classification.classification_condition)})`)),s.id+' classification condition visible');
 const evidence=run(`renderEvidence(S.get(${JSON.stringify(s.id)}))`);
 for(const q of s.questions)assert(evidence.includes(q),s.id+' Q missing');
 for(const m of D.threat_classification_guide.external_mappings.filter(m=>m.scenarios.includes(s.id))){assert(evidence.includes(m.id),s.id+' technique missing');assert(evidence.includes(run(`txt(${JSON.stringify(m.condition)})`)),s.id+' technique condition missing')}
}
for(const type of [...D.threat_classification_guide.types.map(x=>x.id),'failure','context']){
 run(`state.type=${JSON.stringify(type)};render()`);
 const expected=D.scenarios.filter(s=>type==='failure'?s.threat_classification.status==='failure_misconfiguration':type==='context'?s.threat_classification.status==='scope_context':s.threat_classification.types.includes(type));
 assert.deepStrictEqual(JSON.parse(run('JSON.stringify(currentScenarios().map(s=>s.id))')),expected.map(s=>s.id),type+' exact filter');
 assert(elements.get('detail').innerHTML.includes(D.scenario_reader[expected[0].id].title),type+' selected result');
 for(const area of Object.keys(D.areas)){
  run(`state.area=${JSON.stringify(area)};render()`);
  const visible=expected.filter(s=>s.assessment_ids.some(id=>id.split('-')[1]===area));
  assert.equal(run('currentScenarios().length'),visible.length,type+'/'+area+' intersection');
 }
 run("state.area='all'");
}
run("state.type='failure';state.kind='위협 후보';render()");assert.equal(run('currentScenarios().length'),0);assert(elements.get('detail').innerHTML.includes('함께 적용'));
elements.get('reset').click();assert.equal(run('state.type'),'all');assert.equal(run('currentScenarios().length'),83);
// Real delegated click path for a type button, followed by the existing detail tabs.
elements.get('type-filters').listeners.click({target:{closest(){return {dataset:{type:'I'}}}}});assert.equal(run('state.type'),'I');assert.equal(run('currentScenarios().length'),19);
run("setScenario('SC-05-01');handleClick({target:{closest(){return {dataset:{detailTab:'checks'}}}}})");assert(elements.get('detail').innerHTML.includes('관련 점검'));assert.equal(run('state.detailTab'),'checks');
elements.get('reset').click();
// Diagram meaning: exact per-SC touchpoints, reference source and specific logical edges.
const mm=D.map_model,ni=new Set(mm.nodes.map(n=>n.id)),bi=new Set(Object.keys(mm.boundaries));
assert.equal(ni.size,14);assert.equal(mm.edges.length,21);assert.equal(Object.keys(mm.scenario_touchpoints).length,83);
for(const s of D.scenarios){assert.deepStrictEqual(s.map_touchpoints,mm.scenario_touchpoints[s.id]);assert(s.map_touchpoints.nodes.every(n=>ni.has(n)),s.id+' node');assert(s.map_touchpoints.boundaries.every(b=>bi.has(b)),s.id+' boundary');
 run(`Object.assign(state,{type:'all',kind:'all',area:'all',axis:'all',scope:'all',query:'',node:null});setScenario(${JSON.stringify(s.id)})`);
 assert(elements.get('map-selection').innerHTML.includes(s.id),s.id+' map selected SC');
 for(const id of s.map_touchpoints.boundaries)assert(elements.get('map-selection').innerHTML.includes(id),s.id+' boundary explanation');
}
assert.deepStrictEqual(mm.scenario_touchpoints['SC-09-01'].nodes,['approval']);
run("Object.assign(state,{type:'all',kind:'all',area:'all',axis:'all',scope:'all',query:'',node:'change'});render()");assert(!run("currentScenarios().some(s=>s.id==='SC-09-01')"));assert(run("currentScenarios().some(s=>s.id==='SC-09-02')"));
assert(!mm.edges.some(e=>e.source==='source'&&e.target==='approval'));assert(!mm.edges.some(e=>e.source==='approval'&&e.target==='loader'));
assert(mm.edges.some(e=>e.source==='approval'&&e.target==='change'&&e.kind==='organization'));
assert(mm.edges.some(e=>e.source==='caller'&&e.target==='frontend'&&e.kind==='conditional'&&e.condition));
assert(mm.edges.some(e=>e.id==='usage'&&e.source==='worker'&&e.target==='sink'&&e.condition.includes('로그 수집기')));
assert.deepStrictEqual(mm.edges.filter(e=>e.kind==='request').map(e=>[e.source,e.target]),[['caller','gateway'],['gateway','frontend'],['frontend','core'],['core','worker']]);
for(const producer of ['gateway','frontend','core','worker','change'])assert(mm.edges.some(e=>e.source===producer&&e.target==='observe'&&e.kind==='record'&&e.condition));
assert(!/<text[^>]*>TB\d<\/text>/.test(html.match(/data-cell-id="worker"[\s\S]*?<\/g>/)?.[0]??''),'no node TB badge');
elements.get('reset').click();
const mapSource=JSON.parse(fs.readFileSync(path.join(root,'data','threat_map.json'),'utf8'));
assert.deepStrictEqual(D.map_model,mapSource,'map JSON and HTML embed');
const crypto=require('crypto'),integrated=fs.readFileSync(path.join(root,mapSource.source));
assert.equal(crypto.createHash('sha256').update(integrated).digest('hex'),mapSource.source_sha256,'integration source revision');
const svg=fs.readFileSync(path.join(root,'노션_공유용','assets','serving-flow-threat-map.drawio.svg'),'utf8');assert(html.includes(svg),'HTML and editable SVG');
assert.deepStrictEqual([...svg.matchAll(/data-cell-id="([^"]+)"/g)].map(x=>x[1]).sort(),mapSource.nodes.map(n=>n.id).sort(),'SVG node set');
assert.deepStrictEqual([...svg.matchAll(/data-edge-id="([^"]+)"/g)].map(x=>x[1]).sort(),mapSource.edges.map(e=>e.id).sort(),'SVG relation set');
assert.equal(mapSource.visual_groups.length,6);
for(const group of mapSource.visual_groups)for(const id of group.nodes){const n=mapSource.nodes.find(n=>n.id===id);assert(n.x>=group.x&&n.x+n.width<=group.x+group.width&&n.y>=group.y&&n.y+n.height<=group.y+group.height,group.id+'/'+id+' grouping')}
assert(mapSource.edges.find(e=>e.id==='supply').boundaries.includes('TB5'),'delegated read at actual fetch/read');
assert(!mapSource.edges.find(e=>e.id==='startup').boundaries.includes('TB5'),'startup supplies the reference');
const reqLinks=mapSource.edges.filter(e=>e.kind==='request');for(const e of reqLinks)assert(e.points[0][0]<e.points[e.points.length-1][0],'left-to-right request '+e.id);
const notes={passed:true,method:'Source and JavaScript function unit checks; no browser UI access or new rendering verification',scenarios:scs,checks,branches,variantRecords:variants,environmentItems:12,logicalNodes:14,logicalRelations:21,individualScenarioMaps:83,threatTypeFilters:8,typeAreaIntersections:40,callerScenarios:D.scenarios.filter(s=>s.map_touchpoints.nodes.includes('caller')).length,errors:[]};console.log(JSON.stringify(notes,null,2));

// Added criteria and evidence roles must come from the kisaCanonical checklist.
const kisaCanonical=JSON.parse(fs.readFileSync(path.join(root,'data','checklist.json'),'utf8'));
const kisaCanonicalRows=['supply','request_state','platform','lifecycle','observability'].flatMap(a=>kisaCanonical[a+'_assessment_draft']);
for(const c of kisaCanonicalRows){const h=D.checks.find(x=>x.id===c.id);for(const key of ['question','satisfied_if','fail_criteria','unknown_criteria','remediation','evidence_roles','required_subresults'])assert.deepStrictEqual(h[key],c[key],c.id+': '+key);}

for(const s of D.scenarios){const a=s.attack_defense_review,c=canonical.scenarios.find(x=>x.id===s.id);assert.deepStrictEqual(a,c.attack_defense_review,s.id+' attack/defense sync');const out=run(`renderScenario(S.get(${JSON.stringify(s.id)}))`);for(const t of [a.actor_and_initial_right,...a.steps,...['enforcement_point','method','coverage_and_limit'].map(k=>a.defense[k])])assert(out.includes(run(`txt(${JSON.stringify(t)})`)),s.id+' defense visibility');assert.equal(run(`groupTitle(${s.group})`),a.type_name);}

// Primary mechanism filters are disjoint; secondary paths keep their activation condition.
assert.deepStrictEqual(D.mechanism_taxonomy,canonical.threat_mechanism_taxonomy);
for(const t of D.mechanism_taxonomy.types){run(`Object.assign(state,{area:'all',axis:'all',scope:'all',kind:'all',type:'all',query:'',node:null,mechanism:${JSON.stringify(t.id)}});render()`);assert.deepStrictEqual(JSON.parse(run('JSON.stringify(currentScenarios().map(s=>s.id))')).sort(),[...new Set([...t.scenario_ids,...t.related_scenarios.map(v=>v.id)])].sort(),t.id+' mechanism filter');}
run("state.mechanism='context';render()");assert.equal(run('currentScenarios().length'),1);assert.equal(run('currentScenarios()[0].id'),'SC-08-02');
elements.get('reset').click();assert.equal(run('state.mechanism'),'all');assert.equal(run('currentScenarios().length'),83);
for(const s of D.scenarios){assert.deepStrictEqual(s.mechanism_classification,canonical.scenarios.find(v=>v.id===s.id).mechanism_classification);const out=run(`renderScenario(S.get(${JSON.stringify(s.id)}))`);assert(out.includes(run(`txt(${JSON.stringify(s.mechanism_classification.reason)})`)));for(const v of s.mechanism_classification.secondary)assert(out.includes(run(`txt(${JSON.stringify(v.condition)})`)));}

// Eight-function hierarchy: all 36 requirements, 77 stable CHK IDs and shared roles.
assert.deepStrictEqual(D.common_structure,canonical.common_protection_structure,'active protection hierarchy sync');
assert.deepStrictEqual(D.framework_model,canonical.framework_model,'governance and lifecycle sync');
assert.deepStrictEqual(D.reader_hierarchy,canonical.reader_hierarchy,'reader numbering sync');
assert.equal(D.common_structure.functions.length,8);
assert.equal(D.common_structure.questions.length,36);
const activePrimary=D.common_structure.questions.flatMap(r=>r.assessment_ids);
assert.equal(activePrimary.length,77);assert.equal(new Set(activePrimary).size,77);
assert.deepStrictEqual(activePrimary.slice().sort(),D.checks.map(c=>c.id).sort());
const functions=D.common_structure.functions,requirements=D.common_structure.questions;
const reqById=new Map(requirements.map(r=>[r.id,r]));
for(const c of D.checks){
 const src=kisaCanonicalRows.find(v=>v.id===c.id);assert.deepStrictEqual(c,src,c.id+' complete canonical row sync');
 assert.equal(c.parent_purpose,reqById.get(c.purpose_ref.id).purpose);
 assert(requirements.some(r=>r.id===c.purpose_ref.id&&r.assessment_ids.includes(c.id)),c.id+' primary requirement');
 const sid=JSON.stringify(c.scenario_ids[0]),cid=JSON.stringify(c.id);
 run(`state.check=${cid}`);const out=run(`renderChecks(S.get(${sid}))`);
 assert(out.includes(c.display_number),c.id+' new display number');
 assert(out.includes(c.parent_purpose),c.id+' primary requirement visible');
 for(const contribution of c.security_requirement_contributions){assert(out.includes(reqById.get(contribution.requirement_id).purpose),c.id+' supporting requirement visible');}
}
function resetNew(){run("Object.assign(state,{functionId:'all',requirement:'all',area:'all',axis:'all',scope:'all',kind:'all',type:'all',mechanism:'all',query:'',node:null});render()");}
resetNew();
for(const f of functions){
 run(`state.functionId=${JSON.stringify(f.id)};renderRequirementOptions();render()`);
 const visible=D.scenarios.filter(s=>s.assessment_ids.some(id=>{const c=D.checks.find(v=>v.id===id);return c.security_function_id===f.id||c.security_requirement_contributions.some(v=>v.function_id===f.id)}));
 assert.deepStrictEqual(JSON.parse(run('JSON.stringify(currentScenarios().map(s=>s.id))')),visible.map(s=>s.id),f.id+' primary/supporting exact filter');
 assert.equal((elements.get('requirement-filter').innerHTML.match(/<option/g)||[]).length,f.requirement_count+1,f.id+' requirement option count');
 for(const type of D.threat_classification_guide.types){run(`state.type=${JSON.stringify(type.id)};render()`);assert.equal(run('currentScenarios().length'),visible.filter(s=>s.threat_classification.types.includes(type.id)).length,f.id+'/'+type.id+' intersection');}
 run("state.type='all'");
}
for(const r of requirements){
 resetNew();run(`chooseRequirement(${JSON.stringify(r.id)})`);
 const refs=new Set([...r.assessment_ids,...r.related_assessment_ids]);
 const expected=D.scenarios.filter(s=>s.assessment_ids.some(id=>refs.has(id)));
 assert.deepStrictEqual(JSON.parse(run('JSON.stringify(currentScenarios().map(s=>s.id))')),expected.map(s=>s.id),r.id+' primary/supporting exact filter');
 assert(elements.get('requirement-context').innerHTML.includes(r.purpose),r.id+' title');
 assert(elements.get('requirement-context').innerHTML.includes(run(`txt(${JSON.stringify(r.explanation)})`)),r.id+' explanation');
 for(const id of refs)assert(elements.get('requirement-context').innerHTML.includes(id),r.id+' jump check');
}
resetNew();
for(const q of requirements){run(`state.query=${JSON.stringify(q.purpose)};render()`);assert(run('currentScenarios().length')>0,q.id+' searchable by purpose');}
resetNew();
// Exercise actual delegated requirement navigation and jump-to-check interaction.
run("handleClick({target:{closest(){return {dataset:{requirement:'requirement-5-5'}}}}})");
assert.equal(run('state.requirement'),'requirement-5-5');
run("handleClick({target:{closest(){return {dataset:{jumpCheck:'CHK-PLT-008'}}}}})");assert.equal(run('state.check'),'CHK-PLT-008');assert.equal(run('state.detailTab'),'checks');
assert(elements.get('detail').innerHTML.includes('통신'));
elements.get('reset').click();assert.equal(run('state.functionId'),'all');assert.equal(run('state.requirement'),'all');assert.equal(run('currentScenarios().length'),83);
for(const item of D.framework_model.components)assert(elements.get('framework-overview').innerHTML.includes(item.name),item.id+' framework component visible');
for(const item of D.framework_model.lifecycle)assert(elements.get('framework-overview').innerHTML.includes(item.activity),item.name+' lifecycle activity visible');
assert.equal(D.meta.sha256,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'data','checklist.json'))).digest('hex'),'final canonical data hash');
console.log(JSON.stringify({eightFunctionReview:true,functions:8,requirements:36,primaryChecks:77,secondaryContributions:D.checks.reduce((n,c)=>n+c.security_requirement_contributions.length,0),functionStrideIntersections:48,requirementFilterChecks:36,sourceHash:D.meta.sha256},null,2));
