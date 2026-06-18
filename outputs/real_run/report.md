# Lab 16 Benchmark Report

## Metadata
- Dataset: hotpot_100.json
- Mode: cloud
- Records: 200
- Agents: react, reflexion

## Summary
| Metric | ReAct | Reflexion | Delta |
|---|---:|---:|---:|
| EM | 0.92 | 1.0 | 0.08 |
| Avg attempts | 1 | 1.07 | 0.07 |
| Avg token estimate | 1795.24 | 1969.09 | 173.85 |
| Avg latency (ms) | 11628.92 | 11894.92 | 266.0 |

## Failure modes
```json
{
  "react": {
    "none": 92,
    "wrong_final_answer": 7,
    "incomplete_multi_hop": 1
  },
  "reflexion": {
    "none": 100
  },
  "overall": {
    "none": 192,
    "wrong_final_answer": 7,
    "incomplete_multi_hop": 1
  }
}
```

## Extensions implemented
- structured_evaluator
- reflection_memory
- benchmark_report_json
- mock_mode_for_autograding
- adaptive_max_attempts

## Discussion
Reflexion helps when the first attempt stops after the first hop or drifts to a wrong second-hop entity. The tradeoff is higher attempts, token cost, and latency. In a real report, students should explain when the reflection memory was useful, which failure modes remained, and whether evaluator quality limited gains.
