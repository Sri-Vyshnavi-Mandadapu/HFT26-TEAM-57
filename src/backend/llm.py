
import os
import re
import ast
import textwrap
import httpx

from dotenv import load_dotenv


# ==================================================
# 1. LOAD ENVIRONMENT VARIABLES
# ==================================================

load_dotenv()

API_KEY = os.getenv("FEATHERLESS_API_KEY")

BASE_URL = os.getenv(
    "FEATHERLESS_BASE_URL",
    "https://api.featherless.ai/v1"
)

MODEL = os.getenv(
    "FEATHERLESS_MODEL",
    "Qwen/Qwen2.5-Coder-1.5B-Instruct"
)


# ==================================================
# 2. CONFIGURATION
# ==================================================

MAX_SNIPPETS = 10
MAX_CODE_LENGTH = 5000
MAX_RELATED_FUNCTIONS = 4

INSUFFICIENT_EVIDENCE = (
    "I couldn't verify the answer from the retrieved source code. "
    "The available code snippets do not provide enough evidence "
    "to explain this functionality. Try searching for a more "
    "specific function or retrieving additional relevant code."
)


# ==================================================
# 3. FIND FUNCTION CALLS USING AST
# ==================================================

def find_called_functions(code):

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


# ==================================================
# 4. EXPAND RELATED FUNCTIONS
# ==================================================

def expand_related_functions(
    retrieved_results,
    indexed_records,
    max_related=MAX_RELATED_FUNCTIONS
):

    expanded_results = list(retrieved_results)

    seen = {
        (
            item.get("file_path"),
            item.get("symbol"),
            item.get("start_line")
        )
        for item in expanded_results
    }

    related_count = 0

    for result in retrieved_results:

        if related_count >= max_related:
            break

        called_functions = find_called_functions(
            result.get("code", "")
        )

        if not called_functions:
            continue

        # Find Python function implementations.
        candidates = []

        for record in indexed_records:

            if record.get("language", "python").lower() != "python":
                continue

            record_type = (
                record.get("type")
                or record.get("kind")
                or ""
            )

            if record_type not in (
                "function",
                "async_function"
            ):
                continue

            if record.get("symbol") in called_functions:
                candidates.append(record)

        for function_name in called_functions:

            if related_count >= max_related:
                break

            matching_records = [
                record for record in candidates
                if record.get("symbol") == function_name
            ]

            # Prefer a unique function in the same file.
            same_file_matches = [
                record for record in matching_records
                if record.get("file_path") == result.get("file_path")
            ]

            if len(same_file_matches) == 1:

                matching_record = same_file_matches[0]

            elif len(same_file_matches) > 1:

                continue

            elif len(matching_records) == 1:

                matching_record = matching_records[0]

            else:

                # Avoid guessing when function names are ambiguous.
                continue

            key = (
                matching_record.get("file_path"),
                matching_record.get("symbol"),
                matching_record.get("start_line")
            )

            if key in seen:
                continue

            related_record = matching_record.copy()

            related_record["match_reason"] = "Related function"

            expanded_results.append(related_record)

            seen.add(key)
            related_count += 1

    return expanded_results


# ==================================================
# 5. PREPARE EVIDENCE WITH UNIQUE IDS
# ==================================================

def _prepare_evidence(results):

    evidence = []

    for item in results:

        code = item.get("code", "")
        symbol = item.get("symbol", "")

        if not code or not symbol:
            continue

        if len(evidence) >= MAX_SNIPPETS:
            break

        evidence_id = f"E{len(evidence) + 1}"

        evidence.append({

            "id": evidence_id,

            "file_path": item.get(
                "file_path",
                "Unknown"
            ),

            "symbol": symbol,

            "type": (
                item.get("type")
                or item.get("kind")
                or "Unknown"
            ),

            "start_line": item.get(
                "start_line",
                "?"
            ),

            "end_line": item.get(
                "end_line",
                "?"
            ),

            "code": code[:MAX_CODE_LENGTH]

        })

    return evidence


# ==================================================
# 6. BUILD LLM PROMPT
# ==================================================

def _build_prompt(query, evidence):

    evidence_text = ""

    available_functions = []

    for item in evidence:

        symbol = item["symbol"]

        if symbol not in available_functions:
            available_functions.append(symbol)

        evidence_text += f"""
[EVIDENCE {item['id']}]

File: {item['file_path']}
Function: {symbol}
Type: {item['type']}
Lines: {item['start_line']}-{item['end_line']}

Code:
{item['code']}

"""

    functions_text = ", ".join(available_functions)

    prompt = f"""
You are RepoLens AI, a source-code explanation assistant.

Your task is to explain the ACTUAL implementation
of a repository using the supplied source code.

QUESTION:
{query}

FUNCTIONS PRESENT IN THE EVIDENCE:
{functions_text}

RETRIEVED SOURCE CODE:

{evidence_text}


STRICT RULES:

1. Use ONLY the supplied source code as evidence.

2. Read the actual function implementation before explaining it.

3. Never invent functions, files, variables, classes,
   database operations, APIs, or implementation details.

4. Never assume that common features such as registration,
   login, JWT, authentication, sessions, or middleware exist.

5. Do not generate hypothetical code examples.

6. Do not generate code blocks.

7. Do not describe a feature unless the supplied code
   demonstrates that feature.

8. Never invent relationships between independent functions.

9. Never claim that one function calls another unless
   the actual implementation contains that function call.

10. A function being present in the evidence does not mean
    that another function uses or calls it.

11. Distinguish between an actual implementation and
    a function that is merely mentioned.

12. Every sentence explaining code MUST mention the
    corresponding actual function name.

13. Every explanation sentence MUST include the correct
    evidence ID, such as [E1].

14. Use ONLY the evidence IDs supplied above.

15. Never create new evidence IDs.

16. If the retrieved code does not answer the question,
    clearly state that the evidence is insufficient.

17. If only part of the question can be answered,
    explain only the supported part.

18. Do not fill missing implementation details using
    general programming knowledge.

19. Do not assume that two functions are connected
    simply because their names are related.

20. Do not generate a Sources section.
    The application will generate source references.

21. Keep the explanation concise and beginner-friendly.

22. Do not repeat the same explanation.

23. Do not provide a generic programming tutorial.

24. Do not claim that data is stored securely, validated,
    encrypted, or authenticated unless the code demonstrates it.

25. If there is insufficient evidence, return ONLY:
    INSUFFICIENT_EVIDENCE


OUTPUT FORMAT:

Write a short explanation in plain text.

Every sentence describing implementation must:
- Mention the actual function name.
- Include the correct evidence ID.
- Describe only what the supplied code demonstrates.

Do not use headings, numbered steps, bullet points,
or code examples.

ANSWER:
"""

    return prompt


# ==================================================
# 7. CALL FEATHERLESS API
# ==================================================

def _call_llm(prompt):

    if not API_KEY:

        raise ValueError(
            "FEATHERLESS_API_KEY is missing. "
            "Please configure it in your .env file."
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
                "role": "user",
                "content": prompt
            }
        ],

        "temperature": 0.1,

        "max_tokens": 500

    }

    response = httpx.post(
        url,
        headers=headers,
        json=payload,
        timeout=60
    )

    response.raise_for_status()

    data = response.json()

    answer = data["choices"][0]["message"]["content"]

    if not answer or not answer.strip():

        raise ValueError(
            "The LLM returned an empty response."
        )

    return answer.strip()


# ==================================================
# 8. VERIFY FUNCTION-CALL RELATIONSHIPS
# ==================================================

def _has_verified_call_relationship(sentence, evidence):

    call_words = (
        r"\b(call|calls|called|calling|invoke|invokes|invoked)\b"
    )

    if not re.search(
        call_words,
        sentence,
        re.IGNORECASE
    ):
        return True

    # Check whether the sentence claims a relationship
    # between two functions present in the evidence.

    for caller in evidence:

        caller_name = caller["symbol"]

        called_functions = find_called_functions(
            caller["code"]
        )

        for callee in evidence:

            callee_name = callee["symbol"]

            if caller_name == callee_name:
                continue

            # Example: function A calls function B.
            forward_pattern = (
                rf"(?<!\w){re.escape(caller_name)}(?!\w)"
                rf".{{0,100}}?\b(?:calls|invokes)\b"
                rf".{{0,100}}?"
                rf"(?<!\w){re.escape(callee_name)}(?!\w)"
            )

            # Example: function B is called by function A.
            reverse_pattern = (
                rf"(?<!\w){re.escape(callee_name)}(?!\w)"
                rf".{{0,100}}?\b(?:is\s+)?called\s+by\b"
                rf".{{0,100}}?"
                rf"(?<!\w){re.escape(caller_name)}(?!\w)"
            )

            if re.search(
                forward_pattern,
                sentence,
                re.IGNORECASE
            ):

                return callee_name in called_functions

            if re.search(
                reverse_pattern,
                sentence,
                re.IGNORECASE
            ):

                return callee_name in called_functions

    # Reject unverified function-call claims.
    return False


# ==================================================
# 9. ATTACH AND VERIFY SOURCE REFERENCES
# ==================================================

def _attach_sources(answer, evidence):

    if "INSUFFICIENT_EVIDENCE" in answer.upper():

        return INSUFFICIENT_EVIDENCE

    # Remove any Sources section generated by the LLM.
    answer = re.split(
        r"\n\s*(?:#{1,6}\s*)?Sources\s*:",
        answer,
        maxsplit=1,
        flags=re.IGNORECASE
    )[0].strip()

    # Remove existing evidence IDs.
    # The application will attach citations again.
    answer = re.sub(
        r"\[E\d+\]",
        "",
        answer
    ).strip()

    # Split the response into individual sentences.
    sentences = re.split(
        r"(?<=[.!?])\s+|\n+",
        answer
    )

    verified_sentences = []

    used_evidence = {}

    for sentence in sentences:

        sentence = sentence.strip()

        if not sentence:
            continue

        matched_items = []

        # Find actual function names mentioned in the sentence.
        for item in evidence:

            symbol = item["symbol"]

            pattern = (
                rf"(?<!\w){re.escape(symbol)}(?!\w)"
            )

            if re.search(
                pattern,
                sentence
            ):

                matched_items.append(item)

        # Reject sentences without an evidence function.
        if not matched_items:
            continue

        # Verify function-call claims against actual code.
        if not _has_verified_call_relationship(
            sentence,
            evidence
        ):

            print(
                "[VERIFICATION] Rejected unsupported claim:",
                sentence
            )

            continue

        # Generate citations ourselves.
        citation_ids = []

        for item in matched_items:

            evidence_id = item["id"]

            if evidence_id not in citation_ids:
                citation_ids.append(evidence_id)

            used_evidence[evidence_id] = item

        sentence += " " + " ".join(
            f"[{evidence_id}]"
            for evidence_id in citation_ids
        )

        verified_sentences.append(sentence)

    # Reject the answer if no sentence passed verification.
    if not verified_sentences:

        return INSUFFICIENT_EVIDENCE

    answer = " ".join(verified_sentences)

    # Generate Sources section from cited evidence only.
    sources = []

    for evidence_id, item in used_evidence.items():

        sources.append(
            f"- [{evidence_id}] "
            f"`{item['file_path']}` — "
            f"`{item['symbol']}` "
            f"(Lines {item['start_line']}-"
            f"{item['end_line']})"
        )

    return (
        answer
        + "\n\n**Sources:**\n"
        + "\n".join(sources)
    )


# ==================================================
# 10. MAIN EXPLANATION FUNCTION
# ==================================================

def explain(query, retrieved_results, indexed_records=None):

    if not query or not query.strip():

        return "Please provide a valid question."

    if not retrieved_results:

        return INSUFFICIENT_EVIDENCE

    try:

        # Keep only records with actual implementations.
        valid_results = [

            item for item in retrieved_results

            if item.get("code") and item.get("symbol")

        ]

        if not valid_results:

            return INSUFFICIENT_EVIDENCE

        # Expand related functions using actual AST calls.
        if indexed_records is not None:

            retrieved_results = expand_related_functions(
                valid_results,
                indexed_records
            )

        else:

            retrieved_results = valid_results

        # Prepare evidence with unique IDs.
        evidence = _prepare_evidence(
            retrieved_results
        )

        if not evidence:

            return INSUFFICIENT_EVIDENCE

        # Debug: display retrieved evidence.
        print("\n========== RETRIEVED EVIDENCE ==========")

        for item in evidence:

            print(f"""
ID: {item['id']}
File: {item['file_path']}
Symbol: {item['symbol']}
Lines: {item['start_line']}-{item['end_line']}

Code:
{item['code']}
""")

        print("========================================\n")

        # Build prompt.
        prompt = _build_prompt(
            query,
            evidence
        )

        # Generate explanation.
        answer = _call_llm(prompt)

        # Debug: display raw LLM response.
        print("\n===== RAW LLM RESPONSE =====")
        print(answer)
        print("============================")

        # Verify and attach citations.
        final_answer = _attach_sources(
            answer,
            evidence
        )

        return final_answer

    except httpx.HTTPStatusError as error:

        return (
            f"Featherless API error: "
            f"{error.response.status_code} - "
            f"{error.response.text[:500]}"
        )

    except httpx.RequestError as error:

        return f"API connection error: {str(error)}"

    except (KeyError, IndexError, TypeError, ValueError) as error:

        return (
            f"Explanation generation error: {str(error)}"
        )
