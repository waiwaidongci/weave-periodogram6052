import {useEffect,useState} from 'react';
import {Table} from './common/Table';
import {Notice} from './common/Notice';
import {useTask} from '../hooks/useTask';
import {catalogApi} from '../api/catalog';
import {fmt,clock} from '../utils/format';
import type {PointVersionEntry,RevisionBatch,VersionDiff} from '../types/domain';

export function RevisionPanel({study,user,reloadCatalog}:{study:any;user:any;reloadCatalog:()=>Promise<void>}){
 const [versions,setVersions]=useState<PointVersionEntry[]>([]);
 const [batches,setBatches]=useState<RevisionBatch[]>([]);
 const [diff,setDiff]=useState<VersionDiff|null>(null);
 const [left,setLeft]=useState(1),[right,setRight]=useState(1);
 const [raw,setRaw]=useState(''),[device,setDevice]=useState('gauge-01'),[batchNo,setBatchNo]=useState('');
 const task=useTask();
 const [loadError,setLoadError]=useState('');
 const latest=versions[versions.length-1];
 async function refresh(){
  const [v,b]=await Promise.all([catalogApi.versions(study.id),catalogApi.batches(study.id)]);
  setVersions(v);setBatches(b);
  if(v.length){setLeft(v[Math.max(0,v.length-2)].version);setRight(v[v.length-1].version);}
  return {v,b};
 }
 useEffect(()=>{setDiff(null);setLoadError('');refresh().catch(e=>setLoadError(e.message));},[study.id]);
 async function downloadLatest(){
  await task.act(async()=>{await catalogApi.downloadDraft(study.id,latest.version)},'底稿 v'+latest.version+' 已下载（含全部测点与基准版本号）');
 }
 async function loadEditor(){
  const {v}=await refresh();
  const current=v[v.length-1];
  const template={
   batch_no:batchNo||('B-'+new Date().toISOString().slice(0,10).replace(/-/g,'')+'-'+study.id),
   device_id:device,base_version:current.version,
   points:current.points_snapshot.map((p:any)=>({reference:p.reference,position:p.position,thickness:p.thickness}))
  };
  setBatchNo(template.batch_no);setRaw(JSON.stringify(template,null,2));
 }
 async function submit(){
  await task.act(async()=>{
   const payload=JSON.parse(raw);
   try{
    const r=await catalogApi.submitBatch(study.id,payload);
    return r;
   }finally{
    await Promise.all([refresh(),reloadCatalog()]);
   }
  },r=>r.replayed?'重复批次：已回显原接纳结果（未产生新版本）':'批次已接纳并发布为新版本 v'+r.batch.published_version);
 }
 async function showDiff(){
  await task.act(async()=>{const d=await catalogApi.versionDiff(study.id,left,right);setDiff(d);},'版本差异已更新');
 }
 return <section className="revision-panel">
  <small>OFFLINE GAUGE BATCH REVISION</small>
  <h2>离线测厚仪批次回传</h2>
  <p>多台测厚仪基于同一份下载底稿修订：无交叠改动自动合并；同点异值整批拒绝并列出冲突；位置整组互换接纳；同批次号重复回传回显原结果、同号异内容拒绝。每份排队研究只复制一个完整已发布版本。</p>
  <div className="revision-toolbar">
   <span>已发布版本：<strong>v{latest?.version??'—'}</strong>（共 {versions.length} 个）</span>
   <button className="primary" onClick={downloadLatest} disabled={task.busy||!latest}>下载最新底稿 CSV</button>
   {user.role!=='viewer'&&<button onClick={loadEditor} disabled={task.busy}>载入回传模板（基于 v{latest?.version}）</button>}
  </div>
  {user.role!=='viewer'&&<div className="batch-form">
   <div className="field-pair">
    <label>测厚仪编号<input value={device} onChange={e=>setDevice(e.target.value)}/></label>
    <label>批次号（同号幂等）<input value={batchNo} onChange={e=>setBatchNo(e.target.value)} placeholder="例如 gauge-01-20261114-01"/></label>
   </div>
   <label>回传批次 JSON（base_version 为下载底稿版本；points 须含全部测点）<textarea rows={10} value={raw} onChange={e=>setRaw(e.target.value)}/></label>
   <button className="primary" onClick={submit} disabled={task.busy||!raw}>回传批次 →</button>
  </div>}
  <Notice {...task}/>
  {loadError&&<Notice error={loadError}/>}
  <div className="version-diff">
   <h3>版本差异</h3>
   <div className="field-pair">
    <label>左版本<select value={left} onChange={e=>setLeft(+e.target.value)}>{versions.map(v=><option key={v.id} value={v.version}>v{v.version}</option>)}</select></label>
    <label>右版本<select value={right} onChange={e=>setRight(+e.target.value)}>{versions.map(v=><option key={v.id} value={v.version}>v{v.version}</option>)}</select></label>
   </div>
   <button onClick={showDiff} disabled={task.busy}>查看差异</button>
   {diff&&<Table headers={['测点编号','变化','左 位置/厚度','右 位置/厚度']}>
    {diff.changes.length===0&&<tr><td colSpan={4}>两个版本内容一致</td></tr>}
    {diff.changes.map(c=><tr key={c.reference}>
     <td>{c.reference}<small>{c.change==='modified'?'修订':c.change==='added'?'新增':'删除'}</small></td>
     <td>{c.change}</td>
     <td>{c.left?fmt(c.left.position,5)+' / '+fmt(c.left.thickness,7):'—'}</td>
     <td>{c.right?fmt(c.right.position,5)+' / '+fmt(c.right.thickness,7):'—'}</td>
    </tr>)}
   </Table>}
  </div>
  <div className="batch-ledger">
   <h3>批次台账</h3>
   <Table headers={['批次号','设备','基准版本','状态','发布版本','改动点','提交人 / 时间','冲突']}>
    {batches.length===0&&<tr><td colSpan={8}>暂无回传批次</td></tr>}
    {[...batches].reverse().map(b=><tr key={b.id}>
     <td>{b.batch_no}</td><td>{b.device_id}</td><td>v{b.base_version}</td>
     <td><span className={'status '+b.status}>{b.status==='accepted'?'已接纳':'已拒绝'}</span></td>
     <td>{b.published_version?('v'+b.published_version):'—'}</td><td>{b.change_count}</td>
     <td>{b.created_by}<small>{clock(b.created_at)}</small></td>
     <td>{b.conflict_detail?b.conflict_detail.conflicts.map((c:any)=>c.reference+'（'
       +'底 '+fmt(c.base_thickness,7)+' → 传 '+fmt(c.submitted_thickness,7)+'，现 '+fmt(c.current_thickness,7)+'）').join('；'):'—'}</td>
    </tr>)}
   </Table>
  </div>
 </section>}
