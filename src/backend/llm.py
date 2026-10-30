
import os
import re
import ast
import textwrap
import httpx

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("FEATHERLESS_API_KEY")

BASE_URL = os.getenv(
    "FEATHERLESS_BASE_URL",
    "https://api.featherless.ai/v1"
)

MODEL = os.getenv("FEATHERLESS_MODEL")

MAX_SNIPPETS = 10
MAX_CODE_LENGTH = 5000
MAX_RELATED_FUNCTIONS = 4


# --------------------------------------------------
# 1. FIND FUNCTION CALLS IN CODE
# --------------------------------------------------

def find_called_functions(code):
    """
    Find function names called inside a Python code snippet.
    Does not execute the code.
    """

    called_functions = set()

    try:
        tree = ast.parse(textwrap.dedent(code))

        for node in ast.walk(tree):

            if isinstance(node, ast.Call):

                if isinstance(node.func, ast.Name):
                    called_functions.add(node.func.id)

                elif isinstance(node.func, ast.Attribute):
                    called_functions.add(node.func.attr)

    except (SyntaxError, TypeError):
        pass

    return called_functions


# --------------------------------------------------
# 2. EXPAND RELATED FUNCTIONS
# --------------------------------------------------

def expand_related_functions(
    retrieved_results,
    indexed_records,
    max_related=MAX_RELATED_FUNCTIONS
):
    """
    Find implementations of functions called by retrieved code.
    Add those implementations to the evidence.
    """

    expanded_results = list(retrieved_results)

    existing = {
        (
            record.get("file_path"),
            record.get("symbol"),
            record.get("start_line")
        )
        for record in retrieved_results
    }

    related_count = 0

    for record in retrieved_results:

        if record.get("language", "python").lower() != "python":
            continue

        called_functions = find_called_functions(
            record.get("code", "")
        )

        for function_name in called_functions:

            for candidate in indexed_records:

                if candidate.get("symbol") != function_name:
                    continue

                # Only add function implementations.
                if candidate.get("type", "").lower() not in (
                    "function",
                    "async_function"
                ):
                    continue

                if candidate.get("language", "python").lower() != "python":
                    continue

                key = (
                    candidate.get("file_path"),
                    candidate.get("symbol"),
                    candidate.get("start_line")
                )

                if key in existing:
                    continue

                related_record = candidate.copy()
                related_record["match_reason"] = "Related function"

                expanded_results.append(related_record)
                existing.add(key)

                related_count += 1

                if related_count >= max_related:
                    return expanded_results

    return expanded_results


# --------------------------------------------------
# 3. PREPARE EVIDENCE
# --------------------------------------------------

def _prepare_evidence(retrieved_results):
    """
    Prepare code snippets and assign evidence IDs.
    """

    evidence = []

    for i, result in enumerate(
        retrieved_results[:MAX_SNIPPETS],
        start=1
    ):

        code = result.get("code", "")

        if not code:
            continue

        evidence.append({
            "id": f"E{i}",
            "file_path": result.get("file_path", "Unknown file"),
            "symbol": result.get("symbol", "Unknown symbol"),
            "type": result.get("type", "code"),
            "start_line": result.get("start_line", "?"),
            "end_line": result.get("end_line", "?"),
            "code": code[:MAX_CODE_LENGTH]
        })

    return evidence


# --------------------------------------------------
# 4. BUILD LLM PROMPT
# --------------------------------------------------


def _build_prompt(query, evidence):

    evidence_text = ""

    available_functions = [
        item["symbol"] for item in evidence
    ]

    for item in evidence:
        evidence_text += f"""
[EVIDENCE {item['id']}]
File: {item['file_path']}
Function: {item['symbol']}
Lines: {item['start_line']}-{item['end_line']}

Code:
{item['code']}
"""

    prompt = f"""
You are RepoLens AI, a code explanation assistant.

QUESTION:
{query}

FUNCTION IMPLEMENTATIONS PRESENT IN EVIDENCE:
{', '.join(available_functions)}

RETRIEVED SOURCE CODE:
{evidence_text}

STRICT RULES:

1. Use only the supplied source code.
2. Read the actual implementation before explaining a function.
3. A function listed under FUNCTION IMPLEMENTATIONS
   is available. Never call it missing.
4. Do not invent internal behavior.
5. Distinguish function calls from their implementations.
6. Every numbered explanation step MUST contain
   a valid evidence ID such as [E1].
7. Use only evidence IDs supplied above.
8. If a function implementation is available,
   explain what its actual code does.
9. If a called function implementation is unavailable,
   explicitly mention that it was not included in the evidence.
10. Do not generate a Sources section.
    The application will add verified sources.
11. Do not claim a function extracts, validates, filters,
    or parses data unless its implementation demonstrates this.
12. Keep the explanation simple and concise.

ANSWER FORMAT:

The process works as follows:

1. Explain the first operation. [E1]
2. Explain the next operation. [E2]
3. Explain the final operation. [E3]

Missing implementation details:
Mention only functions whose implementations are genuinely
absent from the supplied evidence.

IMPORTANT:
Do not copy the example evidence IDs blindly.
Use the IDs corresponding to the actual code snippets.

ANSWER:
"""

    return prompt



# --------------------------------------------------
# 5. CALL FEATHERLESS API
# --------------------------------------------------

def _call_llm(prompt):
    """
    Send the prompt to the Featherless API.
    """

    if not API_KEY:
        raise ValueError(
            "FEATHERLESS_API_KEY is missing. Check your .env file."
        )

    if not MODEL:
        raise ValueError(
            "FEATHERLESS_MODEL is missing. Add your model ID to .env."
        )

    url = BASE_URL.rstrip("/") + "/chat/completions"

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are RepoLens AI. "
                    "Explain code accurately using only provided evidence."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.1,
        "max_tokens": 800
    }

    response = httpx.post(
        url,
        headers=headers,
        json=payload,
        timeout=60
    )

    response.raise_for_status()

    data = response.json()

    return data["choices"][0]["message"]["content"].strip()


# --------------------------------------------------
# 6. ATTACH VERIFIED SOURCE LOCATIONS
# --------------------------------------------------

def _attach_sources(answer, evidence):
    """
    Attach source locations based on evidence IDs.
    """

    valid_ids = {
        item["id"] for item in evidence
    }

    pattern = r"\[?(E\d+)\]?"

    cited_ids = re.findall(pattern, answer)

    cited_ids = list(dict.fromkeys(
        eid for eid in cited_ids if eid in valid_ids
    ))

    if not cited_ids:

        return (
            answer
            + "\n\nNote: No valid evidence citation was generated. "
            "The explanation could not be verified against "
            "a specific source."
        )

    # Remove any Sources section generated by the model.
    answer = re.split(
        r"\n\s*(?:\*\*)?Sources(?:\*\*)?\s*:",
        answer,
        maxsplit=1,
        flags=re.IGNORECASE
    )[0].strip()

    source_text = "\n\n**Sources:**\n"

    for eid in cited_ids:

        item = next(
            item for item in evidence if item["id"] == eid
        )

        source_text += (
            f"\n- **[{eid}]** `{item['file_path']}`"
            f" — `{item['symbol']}`"
            f" (lines {item['start_line']}-{item['end_line']})"
        )

    return answer + source_text


# --------------------------------------------------
# 7. MAIN EXPLANATION FUNCTION
# --------------------------------------------------

def explain(
    query: str,
    retrieved_results: list[dict],
    indexed_records=None
) -> str:
    """
    Explain code using retrieved snippets and related functions.

    Args:
        query: User's natural-language question.
        retrieved_results: Results returned by the retriever.
        indexed_records: All indexed repository records.

    Returns:
        Explanation with verified source references.
    """

    if not query or not query.strip():

        return "Please enter a question about the codebase."

    if not retrieved_results:

        return (
            "I could not find relevant code in the repository. "
            "Try asking about a specific function, class, or feature."
        )

    # Expand retrieved results with related functions.
    if indexed_records:

        retrieved_results = expand_related_functions(
            retrieved_results,
            indexed_records
        )

    # Prepare evidence after expansion.
    evidence = _prepare_evidence(retrieved_results)

    print("\n========== RETRIEVED EVIDENCE ==========")

    for item in evidence:

        print(f"\nID: {item['id']}")
        print(f"File: {item['file_path']}")
        print(f"Symbol: {item['symbol']}")
        print(f"Lines: {item['start_line']}-{item['end_line']}")
        print("Code:")
        print(item["code"][:1000])

    print("\n========================================\n")

    if not evidence:

        return (
            "The retrieved results do not contain usable code snippets."
        )

    prompt = _build_prompt(query, evidence)

    try:

        answer = _call_llm(prompt)

        return _attach_sources(answer, evidence)

    except httpx.HTTPStatusError as error:

        return (
            f"Featherless API error: HTTP {error.response.status_code}. "
            "Check your model ID, API access, and account credits."
        )

    except httpx.RequestError:

        return (
            "Could not connect to the Featherless API. "
            "Check your internet connection and try again."
        )

    except (KeyError, IndexError, TypeError, ValueError) as error:

        return f"Could not process the LLM response: {error}"
