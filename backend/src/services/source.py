from sqlalchemy import select
from fastapi import HTTPException
from src.models import TraverseStudy,TraversePoint,PointRevisionBatch
from src.utils.serialize import row
from .validation import validate_points
from .audit import record
from .revision import latest_version,ensure_seed_version

def create_study(db,data,actor):
 if db.scalar(select(TraverseStudy).where(TraverseStudy.name==data.name)):raise HTTPException(409,'测线研究名已存在')
 try:points=validate_points([p.model_dump() for p in data.points])
 except ValueError as error:raise HTTPException(422,str(error))
 study=TraverseStudy(**data.model_dump(exclude={'points'}));db.add(study);db.flush()
 for point in points:db.add(TraversePoint(study_id=study.id,**point))
 db.flush()
 ensure_seed_version(db,study,points,actor)
 db.commit();record(db,actor,'study.imported',{'study_id':study.id,'points':len(points),'published_version':1});return row(study)

def source_data(db,id):
 study=db.get(TraverseStudy,id)
 if not study:raise HTTPException(404,'测线研究不存在')
 return row(study),[row(p) for p in db.scalars(select(TraversePoint).where(TraversePoint.study_id==id).order_by(TraversePoint.position))]

def edit_point(db,id,data,actor):
 # 旧修订入口遵守同一版本化规则：封装为单测点批次，基于最新已发布版本回传
 point=db.get(TraversePoint,id)
 if not point:raise HTTPException(404,'测点不存在')
 if point.position==data.position and point.thickness==data.thickness:return row(point)
 study_id=point.study_id
 latest=latest_version(db,study_id)
 snapshot=[dict(p) for p in latest.points_snapshot]
 batch_no=f'legacy-p{id}-v{latest.version}'
 existing=db.scalar(select(PointRevisionBatch).where(PointRevisionBatch.study_id==study_id,
  PointRevisionBatch.batch_no==batch_no))
 if existing:
  if existing.status=='accepted' and existing.published_version:
   raise HTTPException(409,{'detail':'该测点的旧入口修订已发布，请基于最新版本重新修订',
    'published_version':existing.published_version})
  raise HTTPException(409,'该修订批次已存在且未被接纳，请通过批次回传入口重新提交')
 from src.schemas.revision import RevisionBatchInput
 payload=RevisionBatchInput(batch_no=batch_no,device_id='legacy-web-form',base_version=latest.version,
  points=[{**p,'position':data.position if p['reference']==point.reference else p['position'],
          'thickness':data.thickness if p['reference']==point.reference else p['thickness']} for p in snapshot])
 from .revision import submit_batch
 submit_batch(db,study_id,payload,actor)
 refreshed=db.scalar(select(TraversePoint).where(TraversePoint.study_id==study_id,
  TraversePoint.reference==point.reference))
 return row(refreshed)
