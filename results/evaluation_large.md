# Larger evaluation

29 issues: every issue first fixed on or after 2025-01-01 whose fix changed a non-test backend file, except the 21 held-out issues and the development issues. No hand picking. Same rules as the main evaluation: keyword search on the code before the fix, graph history cut at the fix date.

## All issues

| Method | hit@5 | recall@5 |
|---|---|---|
| keyword | 18/29 (62%) | 41% |
| graph | 12/29 (41%) | 24% |
| graph + keyword | 20/29 (69%) | 41% |
| graph + keyword (gated) | 19/29 (66%) | 41% |

## Graph found direct evidence (11 issues)

Decided from the graph result only (top score >= 2.0), never from the answer.

| Method | hit@5 | recall@5 |
|---|---|---|
| keyword | 6/11 (55%) | 39% |
| graph | 6/11 (55%) | 37% |
| graph + keyword | 7/11 (64%) | 38% |
| graph + keyword (gated) | 7/11 (64%) | 38% |

## Graph found no direct evidence (18 issues)

| Method | hit@5 | recall@5 |
|---|---|---|
| keyword | 12/18 (67%) | 43% |
| graph | 6/18 (33%) | 16% |
| graph + keyword | 13/18 (72%) | 43% |
| graph + keyword (gated) | 12/18 (67%) | 43% |

## Graph on vs off, issue by issue (gated method)

- Graph turned a miss into a hit@5: 1 issues
- Graph turned a hit@5 into a miss: 0 issues

