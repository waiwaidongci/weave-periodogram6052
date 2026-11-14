from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import ForeignKey,String,Integer,JSON,DateTime,UniqueConstraint
from datetime import datetime,timezone
from src.config.database import Base

class PointVersion(Base):
 __tablename__='point_versions'
 __table_args__=(UniqueConstraint('study_id','version'),)
 id:Mapped[int]=mapped_column(primary_key=True)
 study_id:Mapped[int]=mapped_column(ForeignKey('traverse_studies.id'))
 version:Mapped[int]=mapped_column(Integer)
 batch_id:Mapped[int|None]=mapped_column(ForeignKey('point_revision_batches.id'),nullable=True)
 points_snapshot:Mapped[list]=mapped_column(JSON)
 created_by:Mapped[str]=mapped_column(String(120))
 created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class PointRevisionBatch(Base):
 __tablename__='point_revision_batches'
 __table_args__=(UniqueConstraint('study_id','batch_no'),)
 id:Mapped[int]=mapped_column(primary_key=True)
 study_id:Mapped[int]=mapped_column(ForeignKey('traverse_studies.id'))
 batch_no:Mapped[str]=mapped_column(String(120))
 device_id:Mapped[str]=mapped_column(String(120))
 base_version:Mapped[int]=mapped_column(Integer)
 published_version:Mapped[int|None]=mapped_column(Integer,nullable=True)
 status:Mapped[str]=mapped_column(String(24),default='accepted')
 payload_hash:Mapped[str]=mapped_column(String(64))
 change_count:Mapped[int]=mapped_column(Integer,default=0)
 conflict_detail:Mapped[dict|None]=mapped_column(JSON,nullable=True)
 created_by:Mapped[str]=mapped_column(String(120))
 created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
