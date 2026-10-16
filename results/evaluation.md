# Evaluation

21 held-out issues. hit@5: at least one fixed file in the top 5. recall@5: share of fixed files in the top 5.

## All issues

| Method | hit@5 | recall@5 |
|---|---|---|
| keyword | 14/21 | 63% |
| graph | 12/21 | 48% |
| graph + keyword | 14/21 | 61% |

## A1: stack trace names the fixed file (4 issues)

| Method | hit@5 | recall@5 |
|---|---|---|
| keyword | 4/4 | 100% |
| graph | 4/4 | 100% |
| graph + keyword | 4/4 | 100% |

## A2: stack trace names a different file (3 issues)

| Method | hit@5 | recall@5 |
|---|---|---|
| keyword | 3/3 | 100% |
| graph | 2/3 | 67% |
| graph + keyword | 2/3 | 67% |

## B: no file named (12 issues)

| Method | hit@5 | recall@5 |
|---|---|---|
| keyword | 5/12 | 36% |
| graph | 4/12 | 24% |
| graph + keyword | 6/12 | 40% |

## C: file named without a trace (2 issues)

| Method | hit@5 | recall@5 |
|---|---|---|
| keyword | 2/2 | 100% |
| graph | 2/2 | 67% |
| graph + keyword | 2/2 | 100% |

## Per issue

First rank of a fixed file (- means not in the list).

| Issue | Group | Fixed files | keyword | graph | graph + keyword |
|---|---|---|---|---|---|
| #25008 | A1 | 2 | 1 | 2 | 2 |
| #23033 | A1 | 1 | 1 | 2 | 1 |
| #22277 | A1 | 1 | 2 | 2 | 2 |
| #21878 | A1 | 2 | 1 | 1 | 1 |
| #21883 | A2 | 1 | 5 | 2 | 3 |
| #20148 | A2 | 1 | 4 | 3 | 3 |
| #18371 | A2 | 1 | 3 | - | 22 |
| #23330 | B | 1 | 8 | - | 8 |
| #25307 | B | 3 | 1 | 1 | 1 |
| #24588 | B | 1 | 2 | 2 | 1 |
| #24315 | B | 1 | 2 | 4 | 3 |
| #22999 | B | 1 | 10 | - | 10 |
| #24470 | B | 1 | - | - | - |
| #24156 | B | 2 | 28 | 2 | 5 |
| #23440 | B | 1 | 49 | - | 49 |
| #22479 | B | 1 | 3 | - | 3 |
| #23492 | B | 2 | 10 | - | 10 |
| #22109 | B | 3 | 8 | - | 8 |
| #21841 | B | 1 | 3 | - | 3 |
| #22726 | C | 3 | 1 | 1 | 1 |
| #25518 | C | 1 | 1 | 1 | 1 |
