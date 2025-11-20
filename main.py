import os
from typing import List, Dict, Any, Optional
from io import BytesIO
import csv
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from schemas import (
    Course,
    SemesterRecord,
    UserProfile,
    GPACalcRequest,
    CGPACalcRequest,
    ProjectionRequest,
    AdviceRequest,
    GPACalcResponse,
    CGPAResponse,
    ProjectionResponse,
    AdviceResponse,
    GRADE_POINTS,
    HONORS_BANDS,
)
from database import db, create_document, get_documents, upsert_document

# Optional heavy import for PDF generation
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False

app = FastAPI(title="GPA & Graduation Planner API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "GPA & Graduation Planner API running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = getattr(db, "name", "unknown")
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["connection_status"] = "Connected"
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:80]}"
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"

    return response


# ---------- Core GPA Logic ----------

def grade_to_points(grade: Optional[str]) -> Optional[float]:
    if grade is None:
        return None
    g = grade.upper()
    if g not in GRADE_POINTS:
        raise HTTPException(status_code=400, detail=f"Invalid grade '{grade}'. Allowed: {list(GRADE_POINTS.keys())}")
    return GRADE_POINTS[g]


def compute_gpa(courses: List[Course]) -> GPACalcResponse:
    total_points = 0.0
    total_credits = 0.0
    seen: set[str] = set()
    for c in courses:
        # Basic duplicate detection by code within a single calc call
        if c.code in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate course entry: {c.code}")
        seen.add(c.code)
        if c.grade is None:
            # Skip planned/in-progress courses in GPA calc
            continue
        pts = grade_to_points(c.grade)
        if pts is None:
            continue
        if c.credit_hours <= 0 or c.credit_hours > 10:
            raise HTTPException(status_code=400, detail=f"Invalid credit hours for {c.code}")
        total_points += pts * c.credit_hours
        total_credits += c.credit_hours
    gpa = round(total_points / total_credits, 3) if total_credits > 0 else 0.0
    return GPACalcResponse(gpa=gpa, total_points=round(total_points, 3), total_credits=round(total_credits, 3))


def compute_cgpa(semesters: List[SemesterRecord]) -> CGPAResponse:
    gpas: List[GPACalcResponse] = []
    agg_points = 0.0
    agg_credits = 0.0
    for sem in semesters:
        res = compute_gpa(sem.courses)
        gpas.append(res)
        agg_points += res.total_points
        agg_credits += res.total_credits
    cgpa = round(agg_points / agg_credits, 3) if agg_credits > 0 else 0.0
    return CGPAResponse(cgpa=cgpa, gpa_by_semester=gpas)


def classify_honors(cgpa: float) -> str:
    # Determine highest classification that cgpa meets (ordered by descending threshold)
    for label, threshold in HONORS_BANDS.items():
        if cgpa >= threshold:
            return label
    return "Fail"


def project_needed_average(completed: List[Course], remaining_credits: float, target_class: str) -> ProjectionResponse:
    if target_class not in HONORS_BANDS:
        raise HTTPException(status_code=400, detail=f"Unknown target_class. Choose one of: {list(HONORS_BANDS.keys())}")
    # current totals
    current = compute_gpa(completed)
    completed_credits = current.total_credits
    completed_points = current.total_points

    if remaining_credits <= 0:
        needed = HONORS_BANDS[target_class]
        return ProjectionResponse(
            target_class=target_class,
            target_cgpa=needed,
            needed_avg_gpa=0.0,
            message="No remaining credits. Your final classification is already determined.",
        )

    target_cgpa = HONORS_BANDS[target_class]
    total_credits_final = completed_credits + remaining_credits
    # Equation: (completed_points + x*remaining_credits) / total_credits_final >= target_cgpa
    required_points_remaining = target_cgpa * total_credits_final - completed_points
    needed_avg_gpa = round(max(0.0, min(4.0, required_points_remaining / remaining_credits)), 3)

    if needed_avg_gpa > 4.0:
        msg = f"Even 4.0 average can't reach {target_class}. Aim for the highest possible and consult advisor."
    else:
        msg = f"You need an average GPA of {needed_avg_gpa} across the remaining credits to achieve {target_class}."

    return ProjectionResponse(
        target_class=target_class,
        target_cgpa=target_cgpa,
        needed_avg_gpa=needed_avg_gpa,
        message=msg,
    )


# ---------- Heuristic AI Advisor ----------

def generate_advice(profile: UserProfile, semesters: List[SemesterRecord]) -> AdviceResponse:
    # Compute per-course and per-category trends
    insights: List[str] = []
    recommendations: List[str] = []
    risk_courses: List[str] = []

    # Semester GPAs and trend
    cgpa_info = compute_cgpa(semesters)
    sem_gpas = [r.gpa for r in cgpa_info.gpa_by_semester if r.total_credits > 0]
    if len(sem_gpas) >= 2:
        if sem_gpas[-1] < sem_gpas[-2] - 0.2:
            insights.append("Recent semester GPA dropped significantly. Consider workload balance and support resources.")
        elif sem_gpas[-1] > sem_gpas[-2] + 0.2:
            insights.append("Great improvement in the latest semester. Keep leveraging what worked.")

    # Identify weak courses (grade points < 2.0)
    category_scores: Dict[str, List[float]] = {}
    for sem in semesters:
        for c in sem.courses:
            if c.grade is None:
                continue
            pts = grade_to_points(c.grade)
            if pts is not None and pts < 2.0:
                risk_courses.append(f"{c.code} ({c.grade})")
            if c.category:
                category_scores.setdefault(c.category.lower(), []).append(pts if pts is not None else 0)

    # Category weaknesses
    for cat, arr in category_scores.items():
        if arr and sum(arr) / len(arr) < 2.3:
            insights.append(f"Performance is weaker in {cat}-related units. Prioritize foundational practice and clinics.")

    # Personalized recommendations and projection
    target = profile.target_class or "Second Class Upper"
    total_credits = sum(c.credit_hours for s in semesters for c in s.courses if c.grade is not None)
    # Assume nominal graduation credits 180 for engineering; configurable per program
    remaining_estimate = max(0.0, 180 - total_credits)
    proj = project_needed_average(
        completed=[c for s in semesters for c in s.courses if c.grade is not None],
        remaining_credits=remaining_estimate,
        target_class=target,
    )
    insights.append(proj.message)

    if risk_courses:
        recommendations.append(
            "Focus on re-assessment of weak units: " + ", ".join(risk_courses[:5]) + ("..." if len(risk_courses) > 5 else "")
        )
    recommendations += [
        "Adopt spaced repetition for theory-heavy units (25-30 minute intervals).",
        "Schedule weekly review blocks and use past papers for targeted practice.",
        "Form a small study group for challenging categories to explain concepts out loud.",
        "Meet course lecturers/TAs early for feedback on weak areas.",
    ]

    return AdviceResponse(insights=insights, recommendations=recommendations, risk_courses=risk_courses)


# ---------- API Endpoints ----------

@app.post("/api/gpa", response_model=GPACalcResponse)
def api_gpa(req: GPACalcRequest):
    try:
        return compute_gpa(req.courses)
    except HTTPException:
        raise
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/cgpa", response_model=Dict[str, Any])
def api_cgpa(req: CGPACalcRequest):
    data = compute_cgpa(req.semesters)
    classification = classify_honors(data.cgpa)
    return {"cgpa": data.cgpa, "gpa_by_semester": [r.model_dump() for r in data.gpa_by_semester], "classification": classification}


@app.post("/api/project", response_model=ProjectionResponse)
def api_project(req: ProjectionRequest):
    return project_needed_average(req.completed, req.remaining_credits, req.target_class)


@app.post("/api/advice", response_model=AdviceResponse)
def api_advice(req: AdviceRequest):
    return generate_advice(req.profile, req.semesters)


# ---------- Export Endpoints ----------

@app.post("/api/export/csv")
def export_csv(req: CGPACalcRequest):
    # Flatten courses with computed GPA per semester
    output = BytesIO()
    writer = csv.writer(output)
    writer.writerow(["Term", "Code", "Name", "Credits", "Grade", "Category"])
    for sem in req.semesters:
        for c in sem.courses:
            writer.writerow([sem.term, c.code, c.name, c.credit_hours, c.grade or "", c.category or ""])
    csv_bytes = output.getvalue()
    return Response(content=csv_bytes, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=transcript.csv"})


@app.post("/api/export/pdf")
def export_pdf(req: CGPACalcRequest):
    if not REPORTLAB_AVAILABLE:
        raise HTTPException(status_code=503, detail="PDF engine not available on server.")
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, height - 40, "GPA Report")
    c.setFont("Helvetica", 10)
    y = height - 70

    cg = compute_cgpa(req.semesters)
    c.drawString(40, y, f"CGPA: {cg.cgpa}")
    y -= 20
    c.drawString(40, y, f"Classification: {classify_honors(cg.cgpa)}")
    y -= 30

    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Courses")
    y -= 18
    c.setFont("Helvetica", 9)
    for sem in req.semesters:
        c.drawString(40, y, f"Term: {sem.term}")
        y -= 16
        for crs in sem.courses:
            line = f" - {crs.code} {crs.name} | {crs.credit_hours} CH | Grade: {crs.grade or '-'}"
            c.drawString(48, y, line)
            y -= 14
            if y < 60:
                c.showPage()
                y = height - 60
                c.setFont("Helvetica", 9)
    c.showPage()
    c.save()

    pdf = buffer.getvalue()
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=gpa_report.pdf"})


# ---------- Persistence (simple CRUD) ----------

@app.post("/api/profile", response_model=Dict[str, Any])
def upsert_profile(profile: UserProfile):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not connected")
    saved = upsert_document("user", {"user_id": profile.user_id}, profile.model_dump())
    return saved


@app.get("/api/profile/{user_id}", response_model=Dict[str, Any])
def get_profile(user_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not connected")
    items = get_documents("user", {"user_id": user_id}, limit=1)
    if not items:
        raise HTTPException(status_code=404, detail="Profile not found")
    return items[0]


@app.post("/api/semesters", response_model=Dict[str, Any])
def save_semesters(user_id: str, semesters: List[SemesterRecord]):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not connected")
    payload = {"user_id": user_id, "semesters": [s.model_dump() for s in semesters]}
    saved = upsert_document("semester", {"user_id": user_id}, payload)
    return saved


@app.get("/api/semesters/{user_id}", response_model=Dict[str, Any])
def load_semesters(user_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not connected")
    items = get_documents("semester", {"user_id": user_id}, limit=1)
    if not items:
        return {"user_id": user_id, "semesters": []}
    return items[0]


# ---------- Self-test endpoint ----------

@app.get("/api/selftest")
def selftest():
    try:
        g = compute_gpa([Course(code="TST101", name="Test", credit_hours=3, grade="A")])
        c = compute_cgpa([
            SemesterRecord(term="S1", courses=[Course(code="T1", name="t1", credit_hours=3, grade="A")]),
            SemesterRecord(term="S2", courses=[Course(code="T2", name="t2", credit_hours=3, grade="B")]),
        ])
        p = project_needed_average([Course(code="TST101", name="Test", credit_hours=3, grade="A")], 9, "Second Class Upper")
        a = generate_advice(UserProfile(user_id="u1", name="User", program="SE"), [
            SemesterRecord(term="S1", courses=[Course(code="MTH", name="Math", credit_hours=3, grade="C", category="math")]),
            SemesterRecord(term="S2", courses=[Course(code="PRG", name="Prog", credit_hours=3, grade="B", category="programming")]),
        ])
        return {"ok": True, "pdf": REPORTLAB_AVAILABLE, "gpa": g.model_dump(), "cgpa": {"cgpa": c.cgpa}, "projection": p.model_dump(), "advice": a.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
