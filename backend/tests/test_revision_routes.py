import os,sys,unittest
os.environ.setdefault('JWT_SECRET','test-secret')
os.environ.setdefault('DATABASE_URL','sqlite:////tmp/ld_route_test.db')
os.environ.setdefault('REDIS_URL','redis://localhost:6379/0')
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.config.database import Base
import src.models
from src.models import TraverseStudy,PointVersion,PointRevisionBatch,User
from src.routes import source,revisions
from src.controllers.dependencies import current_user,writers

engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
Session=sessionmaker(engine,expire_on_commit=False)
Base.metadata.create_all(engine)
actor=User(id=1,email='analyst@lingdu.demo',name='林纬',role='analyst')

app=FastAPI()
app.include_router(source.router);app.include_router(revisions.router)
def override_db():
 db=Session()
 try:yield db
 finally:db.close()
app.dependency_overrides[source.get_db]=override_db
app.dependency_overrides[revisions.get_db]=override_db
app.dependency_overrides[current_user]=lambda:actor
app.dependency_overrides[writers]=lambda:actor
client=TestClient(app)

def study_payload(name='路由测线'):
 return {'name':name,'filename':'r.csv','description':'d',
  'points':[{'reference':f'R-P{i+1:03d}','position':float(i),'thickness':0.5+0.01*i} for i in range(6)]}

class RouteTests(unittest.TestCase):
 def test_01_full_flow_over_http(self):
  r=client.post('/studies',json=study_payload('路由测线-1'));self.assertEqual(r.status_code,200,r.text);sid=r.json()['id']
  # 版本列表与底稿下载
  r=client.get(f'/studies/{sid}/versions');self.assertEqual(r.status_code,200);self.assertEqual([v['version'] for v in r.json()],[1])
  r=client.get(f'/studies/{sid}/draft');self.assertEqual(r.status_code,200);self.assertIn('attachment; filename="traverse-',r.headers['content-disposition'])
  self.assertIn('测点编号',r.text)
  v1=client.get(f'/studies/{sid}/versions/1').json()['points_snapshot']
  # 合法批次（改厚度）
  payload=[{**p,'thickness':0.99} if p['reference']=='R-P001' else p for p in v1]
  r=client.post(f'/studies/{sid}/batches',json={'batch_no':'RB1','device_id':'gauge-7','base_version':1,'points':payload})
  self.assertEqual(r.status_code,200,r.text);body=r.json();self.assertFalse(body['replayed']);self.assertEqual(body['batch']['published_version'],2)
  # 重复批次回显
  r=client.post(f'/studies/{sid}/batches',json={'batch_no':'RB1','device_id':'gauge-7','base_version':1,'points':payload})
  self.assertEqual(r.status_code,200);self.assertTrue(r.json()['replayed'])
  # 同号异内容 409
  other=[{**p,'thickness':0.33} if p['reference']=='R-P002' else p for p in v1]
  r=client.post(f'/studies/{sid}/batches',json={'batch_no':'RB1','device_id':'gauge-7','base_version':1,'points':other})
  self.assertEqual(r.status_code,409);self.assertIn('同批次号',r.json()['detail']['detail'])
  # 同点异值整批拒绝并列出冲突
  stale=[{**p,'thickness':0.12} if p['reference']=='R-P001' else p for p in v1]
  r=client.post(f'/studies/{sid}/batches',json={'batch_no':'RB2','device_id':'gauge-8','base_version':1,'points':stale})
  self.assertEqual(r.status_code,409);self.assertEqual(r.json()['detail']['conflicts'][0]['reference'],'R-P001')
  # 版本差异
  r=client.get(f'/studies/{sid}/version-diff',params={'left':1,'right':2});self.assertEqual(r.status_code,200)
  self.assertEqual([c['reference'] for c in r.json()['changes']],['R-P001'])
  # 批次台账
  r=client.get(f'/studies/{sid}/batches');self.assertEqual(r.status_code,200)
  statuses={b['batch_no']:b['status'] for b in r.json()}
  self.assertEqual(statuses['RB1'],'accepted');self.assertEqual(statuses['RB2'],'rejected')
  # 旧 PATCH 入口同样产生版本
  pid=client.get('/points').json()
  target=next(p for p in pid if p['study_id']==sid and p['reference']=='R-P003')
  r=client.patch(f"/points/{target['id']}",json={'position':target['position'],'thickness':0.66})
  self.assertEqual(r.status_code,200,r.text)
  self.assertEqual(client.get(f'/studies/{sid}/versions').json()[-1]['version'],3)

 def test_02_swap_over_http(self):
  sid=client.post('/studies',json=study_payload('路由测线-2')).json()['id']
  v1=client.get(f'/studies/{sid}/versions/1').json()['points_snapshot']
  p1,p2=v1[0],v1[1];sw=[dict(p) for p in v1]
  sw[0]['position'],sw[1]['position']=p2['position'],p1['position']
  r=client.post(f'/studies/{sid}/batches',json={'batch_no':'SW1','device_id':'g','base_version':1,'points':sw})
  self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['batch']['status'],'accepted')

if __name__=='__main__':unittest.main(verbosity=2)
