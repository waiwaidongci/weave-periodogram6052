from fastapi import APIRouter,Depends,Query
from sqlalchemy.orm import Session
from src.config.database import get_db
from src.controllers.dependencies import current_user,writers
from src.schemas.revision import RevisionBatchInput
from src.services import revision
from src.utils.csv_export import csv_response
router=APIRouter()

@router.get('/studies/{id}/draft')
def draft(id:int,version:int|None=Query(default=None),db:Session=Depends(get_db),user=Depends(current_user)):
 no=version if version is not None else revision.latest_version(db,id).version
 snapshot=revision.get_version(db,id,no)
 rows=[[p['reference'],p['position'],p['thickness']] for p in sorted(snapshot['points_snapshot'],key=lambda p:p['position'])]
 return csv_response(f'traverse-{id}-v{no}-draft.csv',rows,['测点编号','位置mm','厚度mm'])

@router.get('/studies/{id}/versions')
def versions(id:int,db:Session=Depends(get_db),user=Depends(current_user)):return revision.list_versions(db,id)

@router.get('/studies/{id}/versions/{no}')
def one_version(id:int,no:int,db:Session=Depends(get_db),user=Depends(current_user)):return revision.get_version(db,id,no)

@router.get('/studies/{id}/version-diff')
def version_diff(id:int,left:int=Query(...),right:int=Query(...),db:Session=Depends(get_db),user=Depends(current_user)):
 return revision.diff_versions(db,id,left,right)

@router.get('/studies/{id}/batches')
def batches(id:int,db:Session=Depends(get_db),user=Depends(current_user)):return revision.list_batches(db,id)

@router.post('/studies/{id}/batches')
def upload_batch(id:int,data:RevisionBatchInput,db:Session=Depends(get_db),user=Depends(writers)):
 result,replayed=revision.submit_batch(db,id,data,user.email)
 return {'replayed':replayed,'batch':result}
