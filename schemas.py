from typing import List, Optional, Literal
from pydantic import BaseModel, Field, validator

# Each class name corresponds to a MongoDB collection with the lowercased name

GradeLetter = Literal['A','B','C','D','E','F']

GRADE_POINTS = {
    'A': 4.0,
    'B': 3.0,
    'C': 2.0,
    'D': 1.0,
    'E': 0.5,  # some systems use E as 0 or 0.5; we'll allow but treat low
    'F': 0.0,
}

HONORS_BANDS = {
    # JKUAT style classification (approximate; configurable per school):
    # First Class: >= 70% equivalent, we map to CGPA >= 3.7
    'First Class Honors': 3.70,
    'Second Class Upper': 3.30,
    'Second Class Lower': 2.70,
    'Pass': 2.00,
}

class Course(BaseModel):
    code: str = Field(..., min_length=2, max_length=16)
    name: str = Field(..., min_length=2, max_length=64)
    credit_hours: float = Field(..., gt=0, le=10)
    grade: Optional[GradeLetter] = None  # if missing, it's planned or in-progress
    semester: Optional[int] = Field(None, ge=1, le=12)
    category: Optional[str] = Field(None, description="e.g., core, elective, math, programming")

    @validator('code')
    def normalize_code(cls, v: str) -> str:
        return v.strip().upper()

class SemesterRecord(BaseModel):
    term: str  # e.g., "Y2S1" or "2024-1"
    courses: List[Course]

class UserProfile(BaseModel):
    user_id: str
    name: str
    program: str
    target_class: Optional[str] = None  # One of HONORS_BANDS keys

class GPACalcRequest(BaseModel):
    courses: List[Course]

class CGPACalcRequest(BaseModel):
    semesters: List[SemesterRecord]

class ProjectionRequest(BaseModel):
    completed: List[Course] = []
    remaining_credits: float = Field(..., gt=0)
    target_class: str

class AdviceRequest(BaseModel):
    profile: UserProfile
    semesters: List[SemesterRecord]

class GPACalcResponse(BaseModel):
    gpa: float
    total_points: float
    total_credits: float

class CGPAResponse(BaseModel):
    cgpa: float
    gpa_by_semester: List[GPACalcResponse]

class ProjectionResponse(BaseModel):
    target_class: str
    target_cgpa: float
    needed_avg_gpa: float
    message: str

class AdviceResponse(BaseModel):
    insights: List[str]
    recommendations: List[str]
    risk_courses: List[str]
