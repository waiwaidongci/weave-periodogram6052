import {useState} from 'react';import {PageTitle} from '../components/common/PageTitle';import {Notice} from '../components/common/Notice';import {Table} from '../components/common/Table';import {TracePlot} from '../components/TracePlot';import {RevisionPanel} from '../components/RevisionPanel';import {useTask} from '../hooks/useTask';import {catalogApi} from '../api/catalog';import {fmt} from '../utils/format';
const example={name:'不等距纯十八毫米测线',filename:'ld-pure-irregular-18mm.json',description:'独立织片不等距厚度；预设网格包含1/18',points:[0,1.25,2.75,4.25,6,7.5,9.25,11.75,14,16.5,19.25,22,24.5,27.75,31,35].map((position,i)=>({reference:'UI-P'+String(i+1).padStart(2,'0'),position,thickness:.5+.08*Math.cos(2*Math.PI*position/18-.7)}))};
export function SourcePage({studies,points,user,reloadCatalog}:any){
 const [id,S]=useState(0),[raw,R]=useState(JSON.stringify(example,null,2)),task=useTask(),
 study=studies.find(s=>s.id===(id||studies[0]?.id)),rows=points.filter(p=>p.study_id===study?.id);
 return <><PageTitle index="02" title="让位置保持原样，让厚度留下来路。" description="不把不等距位置强制转换为等距FFT；重复位置不能作为独立测点。修订以下载版本为基准按批次合并，不再整表覆盖。"/>
 <label>来源测线<select value={study?.id||''} onChange={e=>S(+e.target.value)}>{studies.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
 {study&&<p>{study.filename} / {study.description}</p>}
 <TracePlot points={rows} title="原始不等距厚度底稿（当前已发布版本）"/>
 <div className="source-layout"><section>
  <h2>观测底稿 / 前24点</h2>
  <Table headers={['编号','位置 mm','厚度 mm','修订']} >{rows.slice(0,24).map(p=><tr key={p.id}><td>{p.reference}</td><td>{fmt(p.position,5)}</td><td>{fmt(p.thickness,7)}</td><td>{user.role!=='viewer'&&<button onClick={()=>{const value=prompt('修订厚度mm（旧入口同样生成版本化批次）',String(p.thickness));if(value!==null)task.act(async()=>{await catalogApi.editPoint(p.id,{position:p.position,thickness:+value});await reloadCatalog()},'测点修订已按批次规则发布为新版本')}}>修订厚度</button>}</td></tr>)}</Table>
 </section>
 <section className="input-sheet"><small>NEW IRREGULAR TRAVERSE</small><h2>导入独立织片测线</h2><p>4–240点，位置唯一、跨度≤10000mm；导入即发布 v1 完整底稿。</p>{user.role!=='viewer'?<form onSubmit={e=>{e.preventDefault();task.act(async()=>{const s=await catalogApi.createStudy(JSON.parse(raw));await reloadCatalog();S(s.id)},'测线研究已保存并发布 v1，可冻结预设频率计划')}}><label>测线 JSON<textarea value={raw} onChange={e=>R(e.target.value)} rows={18}/></label><button className="primary" disabled={task.busy}>保存不等距测线 →</button></form>:<p>观察员只读底稿；测线导入与修订由分析员处理。</p>}<Notice {...task}/></section></div>
 {study&&<div className="section"><RevisionPanel study={study} user={user} reloadCatalog={reloadCatalog}/></div>}
 </>}
