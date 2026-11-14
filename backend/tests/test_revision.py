import os,sys,threading,unittest
os.environ.setdefault('JWT_SECRET','test-secret')
os.environ.setdefault('DATABASE_URL','sqlite:////tmp/ld_revision_test.db')
os.environ.setdefault('REDIS_URL','redis://localhost:6379/0')
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.config.database import Base
import src.models
from src.models import TraverseStudy,TraversePoint,PointVersion,PointRevisionBatch,SpectrumRun
from src.schemas.study import StudyInput
from src.schemas.point import PointPatch
from src.schemas.revision import RevisionBatchInput
from src.services.source import create_study,edit_point,source_data
from src.services.revision import submit_batch,list_versions,diff_versions,latest_version
from fastapi import HTTPException

engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
Session=sessionmaker(engine,expire_on_commit=False)
ACTOR='analyst@lingdu.demo'

def study_input(name='测线A',n=8):
 return StudyInput(name=name,filename='ld-a.csv',description='d',
  points=[{'reference':f'A-P{i+1:03d}','position':float(i),'thickness':0.5+0.01*i} for i in range(n)])

def batch(no,base,points,device='gauge-1'):
 return RevisionBatchInput(batch_no=no,device_id=device,base_version=base,points=points)

def snapshot(db,sid,version=None):
 v=latest_version(db,sid) if version is None else db.scalar(select(PointVersion).where(PointVersion.study_id==sid,PointVersion.version==version))
 return [dict(p) for p in v.points_snapshot]

def with_change(points,ref,**kw):
 out=[dict(p) for p in points]
 for p in out:
  if p['reference']==ref:p.update(kw)
 return out

class RevisionTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):Base.metadata.create_all(engine)
 def setUp(self):
  self.db=Session()
  self.sid=create_study(self.db,study_input(f'测线-{id(self)}-{threading.get_ident()}'),ACTOR)['id']
 def tearDown(self):
  self.db.rollback();self.db.close()

 def test_01_import_publishes_v1(self):
  versions=list_versions(self.db,self.sid)
  self.assertEqual([v['version'] for v in versions],[1])
  _,points=source_data(self.db,self.sid)
  self.assertEqual(len(points),8)
  self.assertEqual(versions[0]['batch_id'],None)

 def test_02_non_overlapping_changes_merge(self):
  base=snapshot(self.db,self.sid)
  dev1=with_change(base,'A-P001',thickness=0.91)
  r,replayed=submit_batch(self.db,self.sid,batch('B1',1,dev1,'g1'),ACTOR)
  self.assertFalse(replayed);self.assertEqual(r['status'],'accepted');self.assertEqual(r['published_version'],2)
  # 第二台设备基于 v1 改另一个点；v2 已含 B1 的改动，应自动合并为 v3
  dev2=with_change(base,'A-P002',thickness=0.92)
  r2,_=submit_batch(self.db,self.sid,batch('B2',1,dev2,'g2'),ACTOR)
  self.assertEqual(r2['status'],'accepted');self.assertEqual(r2['published_version'],3)
  cur={p['reference']:p for p in snapshot(self.db,self.sid)}
  self.assertEqual(cur['A-P001']['thickness'],0.91)
  self.assertEqual(cur['A-P002']['thickness'],0.92)

 def test_03_same_point_different_value_rejects_whole_batch_and_lists_conflict(self):
  base=snapshot(self.db,self.sid)
  submit_batch(self.db,self.sid,batch('C1',1,with_change(base,'A-P001',thickness=0.71)),ACTOR)
  # C2 同点异值 + 另一个无交叠点：整批拒绝，无交叠点也不得生效
  stale=with_change(with_change(base,'A-P001',thickness=0.72),'A-P003',thickness=0.93)
  with self.assertRaises(HTTPException) as ctx:submit_batch(self.db,self.sid,batch('C2',1,stale),ACTOR)
  self.assertEqual(ctx.exception.status_code,409)
  detail=ctx.exception.detail
  self.assertEqual(detail['detail'],'存在同点异值，整批拒绝')
  refs=[c['reference'] for c in detail['conflicts']]
  self.assertEqual(refs,['A-P001'])
  self.assertEqual(detail['conflicts'][0]['current_thickness'],0.71)
  cur={p['reference']:p for p in snapshot(self.db,self.sid)}
  self.assertEqual(cur['A-P003']['thickness'],0.52)  # 无半成品
  stored=self.db.scalar(select(PointRevisionBatch).where(PointRevisionBatch.batch_no=='C2'))
  self.assertEqual(stored.status,'rejected');self.assertIsNone(stored.published_version)
  self.assertEqual(latest_version(self.db,self.sid).version,2)

 def test_04_legal_position_swap_accepted(self):
  base=snapshot(self.db,self.sid)
  p1,p2=next(p for p in base if p['reference']=='A-P001'),next(p for p in base if p['reference']=='A-P002')
  sw=[dict(p) for p in base]
  for p in sw:
   if p['reference']=='A-P001':p['position']=p2['position']
   if p['reference']=='A-P002':p['position']=p1['position']
  r,_=submit_batch(self.db,self.sid,batch('S1',1,sw),ACTOR)
  self.assertEqual(r['status'],'accepted');self.assertEqual(r['published_version'],2)
  cur={p['reference']:p for p in snapshot(self.db,self.sid)}
  self.assertEqual(cur['A-P001']['position'],p2['position'])
  self.assertEqual(cur['A-P002']['position'],p1['position'])

 def test_05_illegal_position_move_rejected(self):
  base=snapshot(self.db,self.sid)
  bad=with_change(base,'A-P001',position=7.5)  # 占用空白位置，不是互换
  with self.assertRaises(HTTPException) as ctx:submit_batch(self.db,self.sid,batch('S2',1,bad),ACTOR)
  self.assertEqual(ctx.exception.status_code,422)
  self.assertEqual(latest_version(self.db,self.sid).version,1)

 def test_06_duplicate_batch_replays_result(self):
  base=snapshot(self.db,self.sid)
  payload=with_change(base,'A-P004',thickness=0.88)
  first,rep1=submit_batch(self.db,self.sid,batch('D1',1,payload),ACTOR)
  self.assertFalse(rep1)
  again,rep2=submit_batch(self.db,self.sid,batch('D1',1,payload),ACTOR)
  self.assertTrue(rep2)
  self.assertEqual(again['id'],first['id']);self.assertEqual(again['published_version'],2)
  self.assertEqual(latest_version(self.db,self.sid).version,2)

 def test_07_same_batch_no_different_content_rejected(self):
  base=snapshot(self.db,self.sid)
  submit_batch(self.db,self.sid,batch('E1',1,with_change(base,'A-P004',thickness=0.88)),ACTOR)
  other=with_change(base,'A-P005',thickness=0.89)
  with self.assertRaises(HTTPException) as ctx:submit_batch(self.db,self.sid,batch('E1',1,other),ACTOR)
  self.assertEqual(ctx.exception.status_code,409)
  self.assertIn('同批次号内容不一致',ctx.exception.detail['detail'])

 def test_08_stale_change_already_superseded_identically_is_no_conflict(self):
  base=snapshot(self.db,self.sid)
  submit_batch(self.db,self.sid,batch('F1',1,with_change(base,'A-P001',thickness=0.61)),ACTOR)
  # 另一设备基于 v1 提交相同目标值：非交叠语义（结果一致），接纳为无变化点跳过冲突
  r,_=submit_batch(self.db,self.sid,batch('F2',1,with_change(base,'A-P001',thickness=0.61)),ACTOR)
  self.assertEqual(r['status'],'accepted')

 def test_09_unknown_reference_and_set_mismatch_rejected(self):
  base=snapshot(self.db,self.sid)
  broken=[dict(p) for p in base];broken[0]['reference']='A-P999'
  with self.assertRaises(HTTPException) as ctx:submit_batch(self.db,self.sid,batch('G1',1,broken),ACTOR)
  self.assertEqual(ctx.exception.status_code,422)
  missing=[dict(p) for p in base[:-1]]
  with self.assertRaises(HTTPException) as ctx:submit_batch(self.db,self.sid,batch('G2',1,missing),ACTOR)
  self.assertEqual(ctx.exception.status_code,422)

 def test_10_no_change_batch_rejected(self):
  base=snapshot(self.db,self.sid)
  with self.assertRaises(HTTPException) as ctx:submit_batch(self.db,self.sid,batch('H1',1,base),ACTOR)
  self.assertEqual(ctx.exception.status_code,422)

 def test_11_legacy_patch_uses_same_engine_and_version_advances(self):
  before=latest_version(self.db,self.sid).version
  point=self.db.scalar(select(TraversePoint).where(TraversePoint.study_id==self.sid,TraversePoint.reference=='A-P001'))
  updated=edit_point(self.db,point.id,PointPatch(position=point.position,thickness=0.77),ACTOR)
  self.assertEqual(updated['thickness'],0.77)
  self.assertEqual(latest_version(self.db,self.sid).version,before+1)
  # 再次旧入口修订同一（已被替换的）点行：基于最新版本生成新批次号，可继续修订
  point2=self.db.scalar(select(TraversePoint).where(TraversePoint.study_id==self.sid,TraversePoint.reference=='A-P001'))
  updated2=edit_point(self.db,point2.id,PointPatch(position=point2.position,thickness=0.78),ACTOR)
  self.assertEqual(updated2['thickness'],0.78)

 def test_12_version_diff_lists_modified_points(self):
  base=snapshot(self.db,self.sid)
  submit_batch(self.db,self.sid,batch('I1',1,with_change(base,'A-P001',thickness=0.31)),ACTOR)
  d=diff_versions(self.db,self.sid,1,2)
  self.assertEqual([c['reference'] for c in d['changes']],['A-P001'])
  self.assertEqual(d['changes'][0]['left']['thickness'],0.5)
  self.assertEqual(d['changes'][0]['right']['thickness'],0.31)

 def test_13_concurrent_batches_serialized_no_lost_update(self):
  base=snapshot(self.db,self.sid)
  errors=[]
  def worker(ref,no,val):
   db=Session()
   try:
    payload=with_change(base,ref,thickness=val)
    submit_batch(db,self.sid,batch(no,1,payload,f'g-{no}'),ACTOR)
   except Exception as e:errors.append(e)
   finally:db.close()
  threads=[threading.Thread(target=worker,args=(f'A-P00{i}',f'K{i}',0.4+i)) for i in range(1,6)]
  for t in threads:t.start()
  for t in threads:t.join()
  self.assertEqual(errors,[])
  cur={p['reference']:p for p in snapshot(self.db,self.sid)}
  for i in range(1,6):self.assertEqual(cur[f'A-P00{i}']['thickness'],0.4+i)
  self.assertEqual(latest_version(self.db,self.sid).version,6)
  accepted=self.db.scalars(select(PointRevisionBatch).where(PointRevisionBatch.study_id==self.sid)).all()
  self.assertTrue(all(b.status=='accepted' for b in accepted))

 def test_18_legacy_patch_noop_is_idempotent(self):
  point=self.db.scalar(select(TraversePoint).where(TraversePoint.study_id==self.sid,TraversePoint.reference=='A-P001'))
  before=latest_version(self.db,self.sid).version
  out=edit_point(self.db,point.id,PointPatch(position=point.position,thickness=point.thickness),ACTOR)
  self.assertEqual(out['thickness'],point.thickness)
  self.assertEqual(latest_version(self.db,self.sid).version,before)  # 无修订不产生新版本

 def test_14_failed_batch_leaves_no_partial_points(self):
  base=snapshot(self.db,self.sid)
  # 构造重复位置的互换破坏：两个点指向同一位置
  bad=[dict(p) for p in base]
  tgt=next(p for p in bad if p['reference']=='A-P003')['position']
  for p in bad:
   if p['reference'] in ('A-P001','A-P002'):p['position']=tgt
  with self.assertRaises(HTTPException):submit_batch(self.db,self.sid,batch('J1',1,bad),ACTOR)
  _,points=source_data(self.db,self.sid)
  self.assertEqual(len(points),8)
  self.assertEqual(len({p['position'] for p in points}),8)

 def test_15_swap_with_thickness_revision_accepted(self):
  base=snapshot(self.db,self.sid)
  p1,p2=next(p for p in base if p['reference']=='A-P001'),next(p for p in base if p['reference']=='A-P002')
  sw=[dict(p) for p in base]
  for p in sw:
   if p['reference']=='A-P001':p['position']=p2['position'];p['thickness']=0.81
   if p['reference']=='A-P002':p['position']=p1['position'];p['thickness']=0.82
  r,_=submit_batch(self.db,self.sid,batch('L1',1,sw),ACTOR)
  self.assertEqual(r['status'],'accepted');self.assertEqual(r['change_count'],2)
  cur={p['reference']:p for p in snapshot(self.db,self.sid)}
  self.assertEqual(cur['A-P001']['position'],p2['position']);self.assertEqual(cur['A-P001']['thickness'],0.81)
  self.assertEqual(cur['A-P002']['position'],p1['position']);self.assertEqual(cur['A-P002']['thickness'],0.82)

 def test_16_validation_failure_keeps_batch_no_reusable(self):
  base=snapshot(self.db,self.sid)
  bad=with_change(base,'A-P001',position=7.5)  # 422，批次未落库
  with self.assertRaises(HTTPException):submit_batch(self.db,self.sid,batch('M1',1,bad),ACTOR)
  good=with_change(base,'A-P001',thickness=0.66)
  r,replayed=submit_batch(self.db,self.sid,batch('M1',1,good),ACTOR)
  self.assertFalse(replayed);self.assertEqual(r['status'],'accepted')

 def test_17_rejected_batch_replay_returns_original_conflict(self):
  base=snapshot(self.db,self.sid)
  submit_batch(self.db,self.sid,batch('N1',1,with_change(base,'A-P001',thickness=0.71)),ACTOR)
  stale=with_change(base,'A-P001',thickness=0.72)
  with self.assertRaises(HTTPException):submit_batch(self.db,self.sid,batch('N2',1,stale),ACTOR)
  # 同内容重发同号：回显原拒绝批次（replayed=True），不再产生新版本
  r,replayed=submit_batch(self.db,self.sid,batch('N2',1,stale),ACTOR)
  self.assertTrue(replayed);self.assertEqual(r['status'],'rejected')
  self.assertIsNone(r['published_version']);self.assertEqual(latest_version(self.db,self.sid).version,2)

if __name__=='__main__':
 unittest.main(verbosity=2)
