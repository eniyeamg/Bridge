import urllib.parse
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import (
    Column,
    Integer,
    String,
    ForeignKey,
    Text,
    Float,
    DateTime,
    create_engine,
    desc,
)
from sqlalchemy.orm import relationship, sessionmaker, Session, declarative_base
from pydantic import BaseModel
from typing import List, Optional

# --- 1. DATABASE CONFIGURATION ---
raw_password = "Ighele1967!?"
encoded_password = urllib.parse.quote_plus(raw_password)

user = "postgres.ofocrqiqrxdrujqrcumx"
host = "aws-1-eu-west-3.pooler.supabase.com"
port = "6543"
db_name = "postgres"

SQLALCHEMY_DATABASE_URL = (
    f"postgresql+psycopg2://{user}:{encoded_password}@{host}:{port}/{db_name}"
)

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# --- 2. SQLALCHEMY MODELS ---
class DealModel(Base):
    __tablename__ = "deals"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    client_name = Column(String, nullable=False)

    revenue_impact = Column(Float, default=0.0)
    currency = Column(String(3), default="GBP")
    created_at = Column(DateTime, default=datetime.utcnow)
    stage = Column(String, default="Discovery")  # Discovery, POC, Legal, Closed Won
    probability = Column(Integer, default=20)  # 20%, 50%, 90%
    forecast_date = Column(DateTime)  # Estimated close date

    requirements = relationship("RequirementModel", back_populates="deal")


class RequirementModel(Base):
    __tablename__ = "requirements"

    id = Column(Integer, primary_key=True, index=True)
    description = Column(Text, nullable=False)
    deal_id = Column(Integer, ForeignKey("deals.id"))

    deal = relationship("DealModel", back_populates="requirements")
    tickets = relationship("EngineeringTicketModel", back_populates="requirement")


class EngineeringTicketModel(Base):
    __tablename__ = "engineering_tickets"

    id = Column(Integer, primary_key=True, index=True)
    priority = Column(String, default="Medium")
    status = Column(String, default="Open")
    requirement_id = Column(Integer, ForeignKey("requirements.id"))

    requirement = relationship("RequirementModel", back_populates="tickets")


# --- 3. PYDANTIC SCHEMAS ---
class RequirementBase(BaseModel):
    description: str


class RequirementCreate(RequirementBase):
    deal_id: int


class Requirement(RequirementBase):
    id: int
    confidence_score: Optional[int] = 0

    class Config:
        from_attributes = True


class DealBase(BaseModel):
    title: str
    client_name: Optional[str] = None
    revenue_impact: Optional[float] = 0.0
    currency: Optional[str] = "GBP"
    stage: Optional[str] = "Discovery"
    probability: Optional[int] = 20
    forecast_date: Optional[datetime] = None


class DealCreate(DealBase):
    pass


class Deal(DealBase):
    id: int
    requirements: List[Requirement] = []

    class Config:
        from_attributes = True


# --- 4. INTERNAL LOGIC ---
def calculate_confidence(requirement_id: int, db: Session):
    tickets = (
        db.query(EngineeringTicketModel)
        .filter(EngineeringTicketModel.requirement_id == requirement_id)
        .all()
    )

    if not tickets:
        return 0

    closed = [t for t in tickets if t.status.lower() == "closed"]
    return int((len(closed) / len(tickets)) * 100)


# --- 5. FASTAPI APP ---
app = FastAPI(title="Deal Flow Orchestrator")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- 6. ENDPOINTS ---
@app.get("/")
def health_check():
    return {"status": "online"}


@app.get("/sales/pipeline/", response_model=List[Deal])
def get_sales_pipeline(db: Session = Depends(get_db)):
    return db.query(DealModel).order_by(desc(DealModel.revenue_impact)).all()


@app.post("/deals/", response_model=Deal)
def create_deal(deal: DealCreate, db: Session = Depends(get_db)):
    db_deal = DealModel(**deal.dict())
    db.add(db_deal)
    db.commit()
    db.refresh(db_deal)
    return db_deal


@app.get("/deals/{deal_id}/ribbon")
def get_revenue_ribbon(deal_id: int, db: Session = Depends(get_db)):
    db_deal = db.query(DealModel).filter(DealModel.id == deal_id).first()
    if not db_deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    # Calculate total requirement progress
    total_confidence = 0
    if db_deal.requirements:
        total_confidence = sum(
            calculate_confidence(r.id, db) for r in db_deal.requirements
        ) / len(db_deal.requirements)

    return {
        "ribbon": {
            "business_value": f"{db_deal.currency} {db_deal.revenue_impact:,.2f}",
            "status_label": f"Stage: {db_deal.stage} ({db_deal.probability}% Probability)",
            "alignment_score": f"{int(total_confidence)}% Tech-Ready",
            "at_risk": total_confidence < 50 and db_deal.probability > 70,
        },
        "message": (
            "Engineering progress is trailing sales probability."
            if (total_confidence < 50 and db_deal.probability > 70)
            else "Aligned"
        ),
    }


@app.post("/requirements/", response_model=Requirement)
def create_requirement_for_deal(req: RequirementCreate, db: Session = Depends(get_db)):
    """Creates a new technical requirement linked to a specific deal."""
    db_req = RequirementModel(description=req.description, deal_id=req.deal_id)
    db.add(db_req)
    db.commit()
    db.refresh(db_req)
    return db_req
