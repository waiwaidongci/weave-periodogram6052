import {request,post,patch,download} from './client';
export const catalogApi={
 studies:()=>request('/studies'),
 points:()=>request('/points'),
 plans:()=>request('/plans'),
 createStudy:(data:any)=>post('/studies',data),
 editPoint:(id:number,data:any)=>patch('/points/'+id,data),
 createPlan:(data:any)=>post('/plans',data),
 versions:(studyId:number)=>request('/studies/'+studyId+'/versions'),
 batches:(studyId:number)=>request('/studies/'+studyId+'/batches'),
 versionDiff:(studyId:number,left:number,right:number)=>request('/studies/'+studyId+'/version-diff?left='+left+'&right='+right),
 downloadDraft:(studyId:number,version:number)=>download('/studies/'+studyId+'/draft?version='+version,'traverse-'+studyId+'-v'+version+'-draft.csv','底稿下载失败，请确认版本存在后重试'),
 submitBatch:(studyId:number,data:any)=>post('/studies/'+studyId+'/batches',data)
};
