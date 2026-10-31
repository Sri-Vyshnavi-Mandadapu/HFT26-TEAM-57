
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from .indexer import index_repository
from .retriever import CodeRetriever
from pathlib import Path
import tempfile
from .llm import explain
from .impact import build_call_graph
import hashlib
import json


app = FastAPI(title="RepoLens AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Stores indexed code records
indexed_records = []
retriever = None
call_graph = None
MAX_UPLOAD_SIZE = 20 * 1024 * 1024

explanation_cache = {}
# TEMPORARY SEARCH FUNCTION
# Replace with CodeRetriever during final integration.



# Request models

class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class ImpactRequest(BaseModel):
    file_path: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    start_line: int | None = None


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
    global indexed_records, retriever, call_graph

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

        file_imports = {}

        try:
            records = index_repository(
                str(zip_path),
                file_imports=file_imports
            )

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

        new_retriever = CodeRetriever(
            records,
            use_semantic=True,
            use_reranker=False
        )

        new_graph = build_call_graph(records, file_imports)

        indexed_records = records
        retriever = new_retriever
        call_graph = new_graph
        explanation_cache.clear()

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

    results = retriever.search(
        query=request.query,
        top_k=request.top_k,
        mode="hybrid"
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

    # Verify that supplied results belong to the indexed repository
    verified = []

    for result in request.results:
        for record in indexed_records:

            fields = (
                "file_path",
                "symbol",
                "start_line",
                "end_line",
                "code"
            )

            if all(
                result.get(k) == record.get(k)
                for k in fields
            ):
                verified.append(record)
                break

    if not verified:
        raise HTTPException(
            status_code=400,
            detail="No verified source evidence provided"
        )

        # Create a unique cache key using the query and retrieved evidence
    cache_data = {
        "query": request.query.strip(),
        "evidence": [
            {
                "file_path": r["file_path"],
                "symbol": r["symbol"],
                "start_line": r["start_line"],
                "code": r["code"]
            }
            for r in verified
        ]
    }

    cache_key = hashlib.sha256(
        json.dumps(cache_data, sort_keys=True).encode()
    ).hexdigest()

    # Return cached explanation if available
    if cache_key in explanation_cache:
        return {
            "explanation": explanation_cache[cache_key],
            "cached": True
        }

    # Generate explanation if not cached
    explanation = explain(
        query=request.query,
        retrieved_results=verified,
        indexed_records=indexed_records
    )

    # Store explanation for future requests
    explanation_cache[cache_key] = explanation

    return {
        "explanation": explanation,
        "cached": False
    }


# Smart Change Impact Analyzer

@app.get("/api/impact/functions")
def list_impact_functions(q: str = "", limit: int = 50):

    if call_graph is None:
        raise HTTPException(
            status_code=400,
            detail="Please upload a repository first"
        )

    limit = max(1, min(limit, 200))

    return {
        "functions": call_graph.list_functions(q, limit),
        "stats": call_graph.stats()
    }


@app.post("/api/impact")
def analyze_impact(request: ImpactRequest):

    if call_graph is None:
        raise HTTPException(
            status_code=400,
            detail="Please upload a repository first"
        )

    target = call_graph.find(
        request.file_path,
        request.symbol,
        request.start_line
    )

    if target is None:
        raise HTTPException(
            status_code=404,
            detail="Function not found in the indexed repository"
        )

    return call_graph.analyze(target)
