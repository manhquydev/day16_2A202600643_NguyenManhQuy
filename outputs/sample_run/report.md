# Lab 16 Benchmark Report

## Metadata
- Dataset: hotpot_100.json
- Mode: mock
- Records: 200
- Agents: react, reflexion

## Summary
| Metric | ReAct | Reflexion | Delta |
|---|---:|---:|---:|
| EM | 1.0 | 1.0 | 0.0 |
| Avg attempts | 1 | 1 | 0 |
| Avg token estimate | 385 | 505 | 120 |
| Avg latency (ms) | 200 | 290 | 90 |

## Failure modes
```json
{
  "react": {
    "none": 100
  },
  "reflexion": {
    "none": 100
  },
  "overall": {
    "none": 200
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
