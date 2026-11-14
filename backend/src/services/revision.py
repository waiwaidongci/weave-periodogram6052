import json,hashlib,threading
from sqlalchemy import select
from fastapi import HTTPException
from src.models import TraverseStudy,TraversePoint,PointVersion,PointRevisionBatch
from src.utils.serialize import row
from .validation import validate_points
from .audit import record

# 进程内按测线串行化回传临界区；PostgreSQL 上 with_for_update 行锁与 (study_id,version)/(study_id,batch_no)
# 唯一约束再提供跨进程/跨实例保护，保证并发回传不丢更新、不产生半成品版本
_study_locks:dict[int,threading.Lock]={}
_locks_guard=threading.Lock()
def _study_lock(study_id):
 with _locks_guard:
  lock=_study_locks.get(study_id)
  if lock is None:
   lock=threading.Lock();_study_locks[study_id]=lock
  return lock

def _payload_hash(study_id,base_version,points):
 payload={'study_id':study_id,'base_version':base_version,
  'points':sorted((p['reference'],p['position'],p['thickness']) for p in points)}
 return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _version_map(version):
 return {p['reference']:p for p in version.points_snapshot}

def _same(a,b):
 return a==b

def latest_version(db,study_id,lock=False):
 stmt=select(PointVersion).where(PointVersion.study_id==study_id).order_by(PointVersion.version.desc())
 if lock:stmt=stmt.with_for_update()
 version=db.scalars(stmt).first()
 if not version:raise HTTPException(409,'测线尚无已发布版本，无法回传修订')
 return version

def _build_changes(base_map,submitted):
 changes=[]
 for item in submitted:
  ref=item['reference']
  if ref not in base_map:raise HTTPException(422,f'测点编号 {ref} 不属于该测线底稿')
  base=base_map[ref]
  if not _same(item['position'],base['position']) or not _same(item['thickness'],base['thickness']):
   changes.append({'reference':ref,'base_position':base['position'],'base_thickness':base['thickness'],
    'position':item['position'],'thickness':item['thickness']})
 return changes

def _validate_position_swaps(changes,base_map):
 # 合法位置互换：被改点占用的新位置集合，必须恰好等于它们在底稿中的旧位置集合（纯置换，无自环）
 moved=[c for c in changes if not _same(c['position'],c['base_position'])]
 if not moved:return
 old=[c['base_position'] for c in moved];new=[c['position'] for c in moved]
 if len(set(new))!=len(new):raise ValueError('互换后位置出现重复')
 if set(old)!=set(new):raise ValueError('位置修订只允许整组互换，不得占用未参与互换测点的位置或新增/删除位置')
 # 每个旧位置须在新集合中且不能指向自身（自环已由 changed 过滤排除，此处双保险）
 for c in moved:
  if c['position'] not in set(old):raise ValueError('位置互换必须在修订测点之间闭合')

def _conflicts(changes,current_map):
 out=[]
 for c in changes:
  cur=current_map.get(c['reference'])
  if cur is None:
   out.append({'reference':c['reference'],'reason':'测点在当前版本不存在','base_position':c['base_position'],
    'base_thickness':c['base_thickness'],'submitted_position':c['position'],'submitted_thickness':c['thickness'],
    'current_position':None,'current_thickness':None});continue
  same_position=_same(c['base_position'],cur['position']);same_thickness=_same(c['base_thickness'],cur['thickness'])
  if same_position and same_thickness:continue
  if _same(c['position'],cur['position']) and _same(c['thickness'],cur['thickness']):continue
  out.append({'reference':c['reference'],
   'reason':'同点异值：其他测厚仪已修订该测点' if not (same_position and same_thickness) else '基准与当前版本不一致',
   'base_position':c['base_position'],'base_thickness':c['base_thickness'],
   'submitted_position':c['position'],'submitted_thickness':c['thickness'],
   'current_position':cur['position'],'current_thickness':cur['thickness']})
 return out

def _publish(db,study,version_no,points,actor,batch):
 # 整表覆盖式写入，但内容来自“当前发布版本 + 本批次交叠合并”的完整快照；失败先抛错，不留半成品
 for existing in db.scalars(select(TraversePoint).where(TraversePoint.study_id==study.id)).all():db.delete(existing)
 db.flush()
 for p in points:db.add(TraversePoint(study_id=study.id,reference=p['reference'],position=p['position'],thickness=p['thickness']))
 db.flush()
 published=PointVersion(study_id=study.id,version=version_no,batch_id=batch.id if batch else None,
  points_snapshot=points,created_by=actor)
 db.add(published);db.flush()
 return published

def submit_batch(db,study_id,data,actor):
 with _study_lock(study_id):
  study=db.get(TraverseStudy,study_id)
  if not study:raise HTTPException(404,'测线研究不存在')
  submitted=[p.model_dump() for p in data.points]
  digest=_payload_hash(study_id,data.base_version,submitted)
  # 同号幂等：先查批次号，再决定回显或拒绝
  existing=db.scalar(select(PointRevisionBatch).where(PointRevisionBatch.study_id==study_id,
   PointRevisionBatch.batch_no==data.batch_no).with_for_update())
  if existing:
   if existing.payload_hash==digest:
    return row(existing),True
   raise HTTPException(409,{'detail':'同批次号内容不一致，拒绝重复提交','existing_batch_id':existing.id,
    'existing_status':existing.status,'existing_hash':existing.payload_hash,'submitted_hash':digest})
  base=db.scalar(select(PointVersion).where(PointVersion.study_id==study_id,
   PointVersion.version==data.base_version).with_for_update())
  if not base:raise HTTPException(422,f'基准版本 v{data.base_version} 不存在，请重新下载底稿')
  latest=latest_version(db,study_id,lock=True)
  base_map=_version_map(base)
  if len({p['reference'] for p in submitted})!=len(submitted):raise HTTPException(422,'批次内测点编号重复')
  if set(base_map)!={p['reference'] for p in submitted}:raise HTTPException(422,'批次测点编号集合必须与底稿完全一致（不增删测点）')
  changes=_build_changes(base_map,submitted)
  if not changes:
   raise HTTPException(422,'批次与基准版本完全相同，没有任何修订；请在下载底稿上修改后再回传')
  try:_validate_position_swaps(changes,base_map)
  except ValueError as error:raise HTTPException(422,str(error))
  current_map=_version_map(latest)
  clash=_conflicts(changes,current_map)
  batch=PointRevisionBatch(study_id=study_id,batch_no=data.batch_no,device_id=data.device_id,
   base_version=data.base_version,payload_hash=digest,change_count=len(changes),created_by=actor)
  if clash:
   batch.status='rejected';batch.published_version=None;batch.conflict_detail={'conflicts':clash}
   db.add(batch);db.commit()
   record(db,actor,'revision.rejected',{'study_id':study_id,'batch_no':data.batch_no,'conflicts':len(clash)})
   raise HTTPException(409,{'detail':'存在同点异值，整批拒绝','batch_id':batch.id,'conflicts':clash})
  # 合并：以当前最新发布版本为底，仅套用本批次改动点
  merged=[dict(p) for p in latest.points_snapshot]
  by_ref={p['reference']:p for p in merged}
  for c in changes:
   by_ref[c['reference']]['position']=c['position'];by_ref[c['reference']]['thickness']=c['thickness']
  try:validate_points(merged)
  except ValueError as error:raise HTTPException(422,str(error))
  next_no=latest.version+1
  db.add(batch);db.flush()
  published=_publish(db,study,next_no,merged,actor,batch)
  batch.status='accepted';batch.published_version=published.version
  db.commit()
  record(db,actor,'revision.accepted',{'study_id':study_id,'batch_no':data.batch_no,
   'base_version':data.base_version,'published_version':published.version,'changes':len(changes)})
  db.refresh(batch)
  return row(batch),False

def list_versions(db,study_id):
 study=db.get(TraverseStudy,study_id)
 if not study:raise HTTPException(404,'测线研究不存在')
 return [row(v) for v in db.scalars(select(PointVersion).where(PointVersion.study_id==study_id).order_by(PointVersion.version))]

def list_batches(db,study_id):
 study=db.get(TraverseStudy,study_id)
 if not study:raise HTTPException(404,'测线研究不存在')
 return [row(b) for b in db.scalars(select(PointRevisionBatch).where(PointRevisionBatch.study_id==study_id).order_by(PointRevisionBatch.id))]

def get_version(db,study_id,no):
 version=db.scalar(select(PointVersion).where(PointVersion.study_id==study_id,PointVersion.version==no))
 if not version:raise HTTPException(404,'版本不存在')
 return row(version)

def diff_versions(db,study_id,left_no,right_no):
 left=db.scalar(select(PointVersion).where(PointVersion.study_id==study_id,PointVersion.version==left_no))
 right=db.scalar(select(PointVersion).where(PointVersion.study_id==study_id,PointVersion.version==right_no))
 if not left or not right:raise HTTPException(404,'版本不存在')
 lm=_version_map(left);rm=_version_map(right);refs=sorted(set(lm)|set(rm))
 items=[]
 for ref in refs:
  a,b=lm.get(ref),rm.get(ref)
  if a is None:items.append({'reference':ref,'change':'added','left':None,'right':b})
  elif b is None:items.append({'reference':ref,'change':'removed','left':a,'right':None})
  elif not (_same(a['position'],b['position']) and _same(a['thickness'],b['thickness'])):
   items.append({'reference':ref,'change':'modified','left':a,'right':b})
 return {'study_id':study_id,'left_version':left_no,'right_version':right_no,
  'left_batch_id':left.batch_id,'right_batch_id':right.batch_id,'changes':items}

def ensure_seed_version(db,study,points,actor):
 if db.scalar(select(PointVersion.id).where(PointVersion.study_id==study.id)):return
 version=PointVersion(study_id=study.id,version=1,batch_id=None,points_snapshot=points,created_by=actor)
 db.add(version);db.flush()
