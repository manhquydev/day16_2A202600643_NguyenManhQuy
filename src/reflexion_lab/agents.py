from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Literal
from .mock_runtime import FAILURE_MODE_BY_QID, actor_answer, evaluator, reflector, reset_telemetry, get_telemetry
from .schemas import AttemptTrace, QAExample, ReflectionEntry, RunRecord

@dataclass
class BaseAgent:
    agent_type: Literal["react", "reflexion"]
    max_attempts: int = 1
    def run(self, example: QAExample) -> RunRecord:
        reflection_memory: list[str] = []
        reflections: list[ReflectionEntry] = []
        traces: list[AttemptTrace] = []
        final_answer = ""
        final_score = 0
        # Determine max attempts dynamically based on difficulty (adaptive_max_attempts)
        max_limit = self.max_attempts
        if self.agent_type == "reflexion":
            difficulty_attempts = {"easy": 2, "medium": 3, "hard": 4}
            max_limit = difficulty_attempts.get(example.difficulty, self.max_attempts)

        for attempt_id in range(1, max_limit + 1):
            reset_telemetry()
            answer = actor_answer(example, attempt_id, self.agent_type, reflection_memory)
            judge = evaluator(example, answer)
            
            if judge.score == 0 and self.agent_type == "reflexion" and attempt_id < max_limit:
                reflection = reflector(example, attempt_id, judge)
                reflections.append(reflection)
                reflection_memory.append(
                    f"Attempt {attempt_id} failed. Reason: {reflection.failure_reason}. "
                    f"Lesson: {reflection.lesson}. Next strategy: {reflection.next_strategy}"
                )
            else:
                reflection = None
                
            # Collect actual telemetry
            tokens_used, latency_incurred = get_telemetry()
            if os.getenv("LLM_PROVIDER", "mock").lower() == "mock":
                token_estimate = 320 + (attempt_id * 65) + (120 if self.agent_type == "reflexion" else 0)
                latency_ms = 160 + (attempt_id * 40) + (90 if self.agent_type == "reflexion" else 0)
            else:
                token_estimate = tokens_used
                latency_ms = latency_incurred
                
            trace = AttemptTrace(attempt_id=attempt_id, answer=answer, score=judge.score, reason=judge.reason, reflection=reflection, token_estimate=token_estimate, latency_ms=latency_ms)
            traces.append(trace)
            final_answer = answer
            final_score = judge.score
            if judge.score == 1:
                break
        total_tokens = sum(t.token_estimate for t in traces)
        total_latency = sum(t.latency_ms for t in traces)
        if final_score == 1:
            failure_mode = "none"
        else:
            if example.qid in FAILURE_MODE_BY_QID:
                failure_mode = FAILURE_MODE_BY_QID[example.qid]
            elif len(traces) > 1 and traces[0].answer == traces[-1].answer:
                failure_mode = "looping"
            else:
                last_trace = traces[-1]
                reason_lower = last_trace.reason.lower()
                if "missing" in reason_lower or "incomplete" in reason_lower or "stop" in reason_lower:
                    failure_mode = "incomplete_multi_hop"
                elif "drift" in reason_lower or "spurious" in reason_lower or "wrong second-hop" in reason_lower:
                    failure_mode = "entity_drift"
                else:
                    failure_mode = "wrong_final_answer"
        return RunRecord(qid=example.qid, question=example.question, gold_answer=example.gold_answer, agent_type=self.agent_type, predicted_answer=final_answer, is_correct=bool(final_score), attempts=len(traces), token_estimate=total_tokens, latency_ms=total_latency, failure_mode=failure_mode, reflections=reflections, traces=traces)

class ReActAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(agent_type="react", max_attempts=1)

class ReflexionAgent(BaseAgent):
    def __init__(self, max_attempts: int = 3) -> None:
        super().__init__(agent_type="reflexion", max_attempts=max_attempts)
