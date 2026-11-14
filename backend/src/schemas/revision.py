from pydantic import BaseModel,Field,ConfigDict
from .point import PointInput

class BatchPointInput(BaseModel):
 model_config=ConfigDict(allow_inf_nan=False,str_strip_whitespace=True)
 reference:str=Field(min_length=1,max_length=80)
 position:float=Field(ge=0,le=1e12,strict=True)
 thickness:float=Field(ge=0,le=1000000,strict=True)

class RevisionBatchInput(BaseModel):
 model_config=ConfigDict(str_strip_whitespace=True)
 batch_no:str=Field(min_length=1,max_length=120)
 device_id:str=Field(min_length=1,max_length=120)
 base_version:int=Field(ge=1)
 points:list[BatchPointInput]=Field(min_length=4,max_length=240)
