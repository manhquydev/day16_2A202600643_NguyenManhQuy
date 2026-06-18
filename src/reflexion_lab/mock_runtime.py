from __future__ import annotations
import os
import re
import json
import time
import urllib.request
import urllib.error
import threading
from dotenv import load_dotenv
from .schemas import QAExample, JudgeResult, ReflectionEntry
from .utils import normalize_answer
from .prompts import ACTOR_SYSTEM, EVALUATOR_SYSTEM, REFLECTOR_SYSTEM

# Load environment variables from .env, overriding system ones
load_dotenv(override=True)

FIRST_ATTEMPT_WRONG = {"hp2": "London", "hp4": "Atlantic Ocean", "hp6": "Red Sea", "hp8": "Andes"}
FAILURE_MODE_BY_QID = {"hp2": "incomplete_multi_hop", "hp4": "wrong_final_answer", "hp6": "entity_drift", "hp8": "entity_drift"}

# Thread-local storage for tracking token count and latency per attempt
_telemetry = threading.local()

def reset_telemetry():
    _telemetry.tokens = 0
    _telemetry.latency_ms = 0

def add_telemetry(tokens: int, latency_ms: int):
    if not hasattr(_telemetry, "tokens"):
        _telemetry.tokens = 0
    if not hasattr(_telemetry, "latency_ms"):
        _telemetry.latency_ms = 0
    _telemetry.tokens += tokens
    _telemetry.latency_ms += latency_ms

def get_telemetry() -> tuple[int, int]:
    tokens = getattr(_telemetry, "tokens", 0)
    latency_ms = getattr(_telemetry, "latency_ms", 0)
    return tokens, latency_ms

def parse_json_from_text(text: str) -> dict:
    text = text.strip()
    # Remove markdown formatting if present
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Regex search for the first valid JSON block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Could not extract JSON from: {text}")

def call_llm(messages: list[dict], temperature: float = 0.0) -> tuple[str, int, int]:
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    if provider == "mock":
        return "", 0, 0
    
    if provider == "cloud":
        url = os.getenv("LLM_URL", "https://opencode.ai/zen/go/v1")
        model = os.getenv("LLM_MODEL", "deepseek-v4-flash")
        api_key = os.getenv("LLM_KEY", "")
    elif provider == "local":
        url = os.getenv("LLM_URL", "http://localhost:11434/v1")
        model = os.getenv("LLM_MODEL", "llama3.2:1b")
        api_key = os.getenv("LLM_KEY", "ollama")
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

    # Ensure correct suffix for standard OpenAI completions
    url = url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = f"{url}/chat/completions"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature
    }
    
    # Use JSON mode if supported or requested for evaluation/reflection
    if any("JSON" in m["content"] for m in messages):
        payload["response_format"] = {"type": "json_object"}
        
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                res_body = response.read().decode("utf-8")
                res_json = json.loads(res_body)
                
                content = res_json["choices"][0]["message"]["content"]
                usage = res_json.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                return content, prompt_tokens, completion_tokens
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8") if e.fp else str(e)
            print(f"[LLM Error] Code {e.code}: {err_body}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(2 ** attempt)
        except Exception as e:
            print(f"[LLM Connection Error]: {str(e)}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(2 ** attempt)
            
    return "", 0, 0

def actor_answer(example: QAExample, attempt_id: int, agent_type: str, reflection_memory: list[str]) -> str:
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    if provider == "mock":
        if example.qid not in FIRST_ATTEMPT_WRONG:
            return example.gold_answer
        if agent_type == "react":
            return FIRST_ATTEMPT_WRONG[example.qid]
        if attempt_id == 1 and not reflection_memory:
            return FIRST_ATTEMPT_WRONG[example.qid]
        return example.gold_answer
    
    # Real LLM call
    context_str = "\n\n".join(f"Title: {c.title}\nText: {c.text}" for c in example.context)
    user_content = f"Context:\n{context_str}\n\nQuestion: {example.question}\n"
    if reflection_memory:
        reflections_str = "\n".join(f"- {ref}" for ref in reflection_memory)
        user_content += f"\nHere are reflections on your previous failed attempts:\n{reflections_str}\n\nPlease learn from these errors and provide the correct answer.\n"
    user_content += "\nFinal Answer:"

    messages = [
        {"role": "system", "content": ACTOR_SYSTEM},
        {"role": "user", "content": user_content}
    ]
    
    start_time = time.perf_counter()
    answer_text, p_tokens, c_tokens = call_llm(messages, temperature=0.0)
    latency_ms = int((time.perf_counter() - start_time) * 1000)
    add_telemetry(p_tokens + c_tokens, latency_ms)
    return answer_text.strip()

def evaluator(example: QAExample, answer: str) -> JudgeResult:
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    if provider == "mock":
        if normalize_answer(example.gold_answer) == normalize_answer(answer):
            return JudgeResult(score=1, reason="Final answer matches the gold answer after normalization.")
        if normalize_answer(answer) == "london":
            return JudgeResult(score=0, reason="The answer stopped at the birthplace city and never completed the second hop to the river.", missing_evidence=["Need to identify the river that flows through London."], spurious_claims=[])
        return JudgeResult(score=0, reason="The final answer selected the wrong second-hop entity.", missing_evidence=["Need to ground the answer in the second paragraph."], spurious_claims=[answer])
    
    # Real LLM call
    context_str = "\n\n".join(f"Title: {c.title}\nText: {c.text}" for c in example.context)
    user_content = (
        f"Context:\n{context_str}\n\n"
        f"Question: {example.question}\n"
        f"Gold Answer: {example.gold_answer}\n"
        f"Predicted Answer: {answer}\n"
    )
    
    messages = [
        {"role": "system", "content": EVALUATOR_SYSTEM},
        {"role": "user", "content": user_content}
    ]
    
    start_time = time.perf_counter()
    res_text, p_tokens, c_tokens = call_llm(messages, temperature=0.0)
    latency_ms = int((time.perf_counter() - start_time) * 1000)
    add_telemetry(p_tokens + c_tokens, latency_ms)
    
    try:
        data = parse_json_from_text(res_text)
        return JudgeResult(
            score=int(data.get("score", 0)),
            reason=data.get("reason", "Graded via LLM"),
            missing_evidence=data.get("missing_evidence", []),
            spurious_claims=data.get("spurious_claims", [])
        )
    except Exception as e:
        print(f"[Evaluator Parse Error]: {e}. Raw response: {res_text}")
        is_correct = normalize_answer(example.gold_answer) == normalize_answer(answer)
        return JudgeResult(
            score=1 if is_correct else 0,
            reason=f"Fallback matching due to parsing error. Correct: {is_correct}. Raw: {res_text}",
            missing_evidence=[],
            spurious_claims=[]
        )

def reflector(example: QAExample, attempt_id: int, judge: JudgeResult) -> ReflectionEntry:
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    if provider == "mock":
        strategy = "Do the second hop explicitly: birthplace city -> river through that city." if example.qid == "hp2" else "Verify the final entity against the second paragraph before answering."
        return ReflectionEntry(attempt_id=attempt_id, failure_reason=judge.reason, lesson="A partial first-hop answer is not enough; the final answer must complete all hops.", next_strategy=strategy)
    
    # Real LLM call
    context_str = "\n\n".join(f"Title: {c.title}\nText: {c.text}" for c in example.context)
    user_content = (
        f"Context:\n{context_str}\n\n"
        f"Question: {example.question}\n"
        f"Gold Answer: {example.gold_answer}\n"
        f"Grader Evaluation Reason: {judge.reason}\n"
        f"Attempt Number: {attempt_id}\n"
    )
    
    messages = [
        {"role": "system", "content": REFLECTOR_SYSTEM},
        {"role": "user", "content": user_content}
    ]
    
    start_time = time.perf_counter()
    res_text, p_tokens, c_tokens = call_llm(messages, temperature=0.0)
    latency_ms = int((time.perf_counter() - start_time) * 1000)
    add_telemetry(p_tokens + c_tokens, latency_ms)
    
    try:
        data = parse_json_from_text(res_text)
        return ReflectionEntry(
            attempt_id=attempt_id,
            failure_reason=data.get("failure_reason", judge.reason),
            lesson=data.get("lesson", "Incorrect reasoning path used."),
            next_strategy=data.get("next_strategy", "Focus on extracting both hops sequentially.")
        )
    except Exception as e:
        print(f"[Reflector Parse Error]: {e}. Raw response: {res_text}")
        return ReflectionEntry(
            attempt_id=attempt_id,
            failure_reason=judge.reason,
            lesson="Failed to parse reflection JSON.",
            next_strategy="Analyze supporting paragraphs and verify connections before outputting."
        )
