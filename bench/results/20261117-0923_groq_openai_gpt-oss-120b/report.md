# Text-to-SQL benchmark report

- Model: `groq/openai/gpt-oss-120b`
- Questions: 45 (`questions.json`)
- Database: `retail.db`
- Run time: 1381s
- Metric: execution accuracy (see `bench/metrics.py` for comparison rules)

## Execution accuracy

Questions that hit an API error (rate limit, outage) are not scored; see the last column.

| Config | Overall | easy (n=15) | medium (n=18) | hard (n=12) | Scored |
|---|---|---|---|---|---|
| baseline | **64.4%** | 93.3% | 61.1% | 33.3% | 45/45 |
| context | **97.8%** | 100.0% | 100.0% | 91.7% | 45/45 |
| fewshot | **100.0%** | 100.0% | 100.0% | 100.0% | 45/45 |
| full | **100.0%** | 100.0% | 100.0% | 100.0% | 45/45 |

## Reliability and cost (per scored question)

| Config | Executes | Fixed by self-correction | Schema-linking recall | Avg tables in prompt | Avg LLM calls | Avg prompt tokens | Median latency (s) |
|---|---|---|---|---|---|---|---|
| baseline | 97.8% | 0 | 100% | 7.0 | 1.00 | 639 | 3.52 |
| context | 100.0% | 0 | 100% | 7.0 | 1.00 | 1,378 | 10.73 |
| fewshot | 100.0% | 0 | 100% | 7.0 | 1.00 | 1,891 | 14.81 |
| full | 100.0% | 0 | 100% | 4.4 | 1.00 | 1,571 | 7.79 |

## baseline → full

- Newly solved (16): e02, h01, h02, h03, h04, h07, h08, h11, h12, m01, m02, m06, m09, m11, m15, m17
- Regressions (0): —
