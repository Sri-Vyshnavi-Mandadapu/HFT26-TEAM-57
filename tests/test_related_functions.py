
import sys
from pathlib import Path

# Add project root to Python path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.llm import expand_related_functions

# Simulated initially retrieved result
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

# Simulated complete indexed repository
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

# Run related-function expansion
expanded = expand_related_functions(
    retrieved_results,
    indexed_records
)

# Display results
print("\nRELATED FUNCTION EXPANSION TEST")
print("--------------------------------")

for record in expanded:
    print("Function:", record["symbol"])
    print("File:", record["file_path"])
    print("Reason:", record.get("match_reason", "Initially retrieved"))
    print()
    
# Verify expected results
found_functions = {record["symbol"] for record in expanded}

assert "index_repository" in found_functions
assert "extract_zip" in found_functions
assert "parse_file" in found_functions

print("SUCCESS: Related functions were retrieved!")
