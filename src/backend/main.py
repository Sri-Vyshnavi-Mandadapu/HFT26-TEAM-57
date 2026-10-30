
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pathlib import Path
import tempfile

app = FastAPI(title="RepoLens AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Stores indexed code records
indexed_records = []

MAX_UPLOAD_SIZE = 20 * 1024 * 1024


# TEMPORARY FUNCTIONS
# Replace with teammates' modules during integration.

def index_repository(zip_path: str) -> list[dict]:
    return [
        {
            "file_path": "src/auth.py",
            "symbol": "login_user",
            "type": "function",
            "start_line": 1,
            "end_line": 3,
            "code": (
                "def login_user(username, password):\n"
                "    return verify_password(username, password)"
            ),
            "language": "python"
        }
    ]


def search(query: str, records: list[dict], top_k: int = 5):
    return [
        {**record, "score": 1.0}
        for record in records[:top_k]
    ]


def explain(query: str, results: list[dict]) -> str:
    return "Mock explanation: this function handles user login."


# Request models

class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class ExplainRequest(BaseModel):
    query: str = Field(min_length=1)
    results: list[dict]


# API endpoints

@app.get("/")
def home():
    return {"message": "RepoLens AI backend running"}


@app.get("/api/status")
def get_status():
    return {
        "indexed": len(indexed_records) > 0,
        "total_records": len(indexed_records)
    }


@app.post("/api/index")
async def upload_repository(file: UploadFile = File(...)):
    global indexed_records

    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a ZIP file"
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        zip_path = Path(temp_dir) / "repository.zip"

        content = await file.read(MAX_UPLOAD_SIZE + 1)

        if len(content) > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413,
                detail="ZIP file exceeds 20 MB"
            )

        zip_path.write_bytes(content)

        try:
            records = index_repository(str(zip_path))
        except (ValueError, OSError) as e:
            raise HTTPException(
                status_code=400,
                detail=str(e)
            )

        if not records:
            raise HTTPException(
                status_code=422,
                detail="No supported source files found"
            )

        indexed_records = records

    return {
        "message": "Repository indexed successfully",
        "total_records": len(indexed_records)
    }


@app.post("/api/search")
def search_repository(request: SearchRequest):
    if not indexed_records:
        raise HTTPException(
            status_code=400,
            detail="Please upload a repository first"
        )

    results = search(
        request.query,
        indexed_records,
        request.top_k
    )

    return {
        "query": request.query,
        "results": results
    }


@app.post("/api/explain")
def explain_code(request: ExplainRequest):
    if not indexed_records:
        raise HTTPException(
            status_code=400,
            detail="Please index a repository first"
        )

    # Only accept results that match records in our index.
    verified = []
    for result in request.results:
        for record in indexed_records:
            fields = ("file_path", "symbol", "start_line",
                      "end_line", "code")
            if all(result.get(k) == record.get(k) for k in fields):
                verified.append(record)
                break

    if not verified:
        raise HTTPException(
            status_code=400,
            detail="No verified source evidence provided"
        )

    explanation = explain(request.query, verified)

    return {"explanation": explanation}
