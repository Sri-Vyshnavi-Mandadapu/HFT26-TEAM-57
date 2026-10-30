from fastapi import FastAPI

app = FastAPI(title="RepoLens AI")

@app.get("/")
def home():
    return {"message": "RepoLens AI backend running"}