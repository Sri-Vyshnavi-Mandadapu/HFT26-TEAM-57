from src.backend.llm import explain


sample_results = [
    {
        "file_path": "src/indexer.py",
        "symbol": "index_repository",
        "type": "function",
        "start_line": 10,
        "end_line": 35,
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


question = "How does the repository indexing process work?"

answer = explain(question, sample_results)

print("\nQUESTION:")
print(question)

print("\nANSWER:")
print(answer)