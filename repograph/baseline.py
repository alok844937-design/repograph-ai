"""Keyword-search baseline with no graph.

Ranks backend files the way a developer would search without a graph:
first any file the issue text names directly (for example in a stack
trace), then BM25 over the file contents using the issue's words.

For a fair test the index is built from the code as it was just before
the issue's fix. Searching today's code would let the fix's own words
leak into the results.

Usage:
  python -m repograph.baseline 25307
"""

import argparse
import math
import re
from collections import Counter

from repograph import config
from repograph.ingest_code import git
from repograph.investigate import PATH_PATTERN, is_candidate, words

TOP_K = 10
K1 = 1.2
B = 0.75
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def code_tokens(text):
    """Splits code into lowercase words, including identifier parts."""
    tokens = []
    for identifier in IDENTIFIER.findall(text):
        for part in identifier.split("_"):
            for piece in CAMEL_BOUNDARY.split(part):
                if len(piece) >= 3:
                    tokens.append(piece.lower())
    return tokens


def candidate_files(revision):
    """Returns candidate backend files at a git revision."""
    paths = git("ls-tree", "-r", "--name-only", revision).split("\n")
    return sorted(p for p in paths if is_candidate(p) and (
        p == "main.py" or (p.startswith("core/")
                           and not p.startswith("core/templates/"))))


class KeywordIndex:
    """BM25 index over candidate backend files at one git revision."""

    def __init__(self, revision=None):
        self.revision = revision or config.OPPIA_BRANCH
        self.docs = {}
        for path in candidate_files(self.revision):
            text = git("show", f"{self.revision}:{path}")
            self.docs[path] = Counter(code_tokens(text))
        self.lengths = {p: sum(c.values()) for p, c in self.docs.items()}
        self.average = sum(self.lengths.values()) / max(len(self.docs), 1)
        self.doc_freq = Counter()
        for counts in self.docs.values():
            self.doc_freq.update(counts.keys())

    def bm25(self, path, query):
        counts, length = self.docs[path], self.lengths[path]
        total = len(self.docs)
        score = 0.0
        for term in query:
            tf = counts.get(term, 0)
            if not tf:
                continue
            idf = math.log(1 + (total - self.doc_freq[term] + 0.5)
                           / (self.doc_freq[term] + 0.5))
            score += idf * tf * (K1 + 1) / (
                tf + K1 * (1 - B + B * length / self.average))
        return score

    def search(self, text, top_k=TOP_K):
        """Returns [(path, score, reason)] for the issue text."""
        named = set(PATH_PATTERN.findall(text)) & set(self.docs)
        query = set(words(text))
        scored = []
        for path in self.docs:
            score = self.bm25(path, query)
            reason = "keyword match"
            if path in named:
                score += 1000.0
                reason = "issue text names this file"
            if score > 0:
                scored.append((path, round(score, 2), reason))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:top_k]


def main():
    from repograph.db import get_graph
    from repograph.investigate import load_issue

    parser = argparse.ArgumentParser()
    parser.add_argument("issue", type=int)
    parser.add_argument("--revision", help="git revision to search, "
                        "for example a fix commit followed by ^")
    args = parser.parse_args()

    title, body, _ = load_issue(get_graph(), args.issue)
    index = KeywordIndex(args.revision)
    print(f"Issue #{args.issue}: {title}")
    print(f"Keyword search over {index.revision}, no graph\n")
    for rank, (path, score, reason) in enumerate(
            index.search(f"{title}\n{body}"), start=1):
        print(f"{rank}. {path}  (score {score}, {reason})")


if __name__ == "__main__":
    main()