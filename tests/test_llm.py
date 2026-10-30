
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.llm import explain

print("Starting RepoLens AI LLM test...")

# Simulated retrieved code
retrieved_results = [
    {
        "file_path": "src/indexer.py",
        "symbol": "index_repository",
        "type": "function",
        "language": "python",
        "start_line": 10,
        "end_line": 20,
        "code": """
def index_repository(zip_path):
    files = extract_zip(zip_path)
    records = []

    for file in files:
        parsed_data = parse_file(file)
        records.extend(parsed_data)

    return records
"""
    }
]

# Complete indexed records
indexed_records = [
    {
        "file_path": "src/indexer.py",
        "symbol": "index_repository",
        "type": "function",
        "language": "python",
        "start_line": 10,
        "end_line": 20,
        "code": """
def index_repository(zip_path):
    files = extract_zip(zip_path)
    records = []

    for file in files:
        parsed_data = parse_file(file)
        records.extend(parsed_data)

    return records
"""
    },
    {
        "file_path": "src/utils.py",
        "symbol": "extract_zip",
        "type": "function",
        "language": "python",
        "start_line": 5,
        "end_line": 12,
        "code": """
def extract_zip(zip_path):
    return ["file1.py", "file2.py"]
"""
    },
    {
        "file_path": "src/parser.py",
        "symbol": "parse_file",
        "type": "function",
        "language": "python",
        "start_line": 15,
        "end_line": 25,
        "code": """
def parse_file(file):
    return [{"symbol": "sample_function", "code": file}]
"""
    }
]

query = "How does the repository indexing process work?"

print("\nSending question to Featherless API...")

answer = explain(
    query,
    retrieved_results,
    indexed_records
)

print("\n========== LLM ANSWER ==========")
print(answer)
print("\n========== TEST COMPLETED ==========")
