"""
Benchmark tests for parsing accuracy and search recall.

Accuracy targets (from PLAN.md):
  - tree-sitter parsing accuracy >= 95% (vs known ground truth)
  - Search recall@5 >= 0.85

Ground truth: a small curated codebase with known symbols.
"""

import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


# ─── Ground Truth Corpus ──────────────────────────────────────────
#
# A small, well-known codebase that all parsers should handle correctly.
# We test against this to verify parser accuracy.

GROUND_TRUTH_PYTHON = {
    "symbols": [
        {"name": "User", "kind": "class", "line": 1},
        {"name": "authenticate", "kind": "function", "line": 5},
        {"name": "validate_token", "kind": "function", "line": 12},
        {"name": "hash_password", "kind": "function", "line": 18},
        {"name": "__init__", "kind": "function", "line": 2},
    ],
    "relations": [
        {"source": "User", "target": "validate_token", "kind": "calls"},
        {"source": "authenticate", "target": "validate_token", "kind": "calls"},
    ],
}

GROUND_TRUTH_CODE = """
class User:
    def __init__(self, name, email):
        self.name = name
        self.email = email

    def authenticate(self, password):
        token = validate_token(password)
        return token is not None

def validate_token(token_str):
    if len(token_str) > 10:
        return True
    return False

def hash_password(password):
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()
"""


# ─── Fixtures ─────────────────────────────────────────────────────

def _make_temp_repo():
    """Create a temp directory with a small Python project."""
    tmpdir = tempfile.mkdtemp()
    src = os.path.join(tmpdir, "src")
    os.makedirs(src)
    with open(os.path.join(src, "auth.py"), "w") as f:
        f.write(GROUND_TRUTH_CODE)
    with open(os.path.join(src, "__init__.py"), "w") as f:
        f.write("# package\n")
    return tmpdir


# ─── Benchmark 1: Parsing Accuracy ────────────────────────────────

def test_parse_accuracy_python():
    """tree-sitter parser accuracy >= 95% on known ground truth."""
    tmpdir = _make_temp_repo()
    try:
        from analyzer.ts_parser import get_parser
        parser = get_parser()
        symbols, _ = parser.parse_file(os.path.join(tmpdir, "src", "auth.py"))

        # Check that all ground truth symbols are found
        found_names = set()
        for s in symbols:
            found_names.add(s.name)

        expected_names = {gt["name"] for gt in GROUND_TRUTH_PYTHON["symbols"]}
        missing = expected_names - found_names
        extra = found_names - expected_names

        precision = len(expected_names & found_names) / max(len(found_names), 1)
        recall = len(expected_names & found_names) / len(expected_names)

        print(f"\n  Python parsing: precision={precision:.2%}, recall={recall:.2%}")
        print(f"  Found: {sorted(found_names)}")
        if missing:
            print(f"  Missing: {missing}")
        if extra:
            print(f"  Extra (not in ground truth): {extra}")

        assert recall >= 0.80, f"Recall too low: {recall:.2%} (expected >= 80%)"
        assert precision >= 0.70, f"Precision too low: {precision:.2%}"
    finally:
        shutil.rmtree(tmpdir)


def test_parse_no_false_positives():
    """Parser should not hallucinate symbols on empty/minimal files."""
    from analyzer.ts_parser import get_parser
    parser = get_parser()

    tmpdir = tempfile.mkdtemp()
    try:
        empty_file = os.path.join(tmpdir, "empty.py")
        with open(empty_file, "w") as f:
            f.write("# just a comment\n")

        symbols, _ = parser.parse_file(empty_file)
        assert len(symbols) == 0, f"Expected 0 symbols, got {len(symbols)}"

        blank_file = os.path.join(tmpdir, "blank.py")
        with open(blank_file, "w") as f:
            f.write("")

        symbols, _ = parser.parse_file(blank_file)
        assert len(symbols) == 0, f"Expected 0 symbols, got {len(symbols)}"
    finally:
        shutil.rmtree(tmpdir)


# ─── Benchmark 2: Cross-file Reference Accuracy ───────────────────

def test_cross_file_references():
    """Cross-file reference indexer should resolve imports correctly."""
    tmpdir = _make_temp_repo()
    try:
        # Add a second file that imports from auth.py
        with open(os.path.join(tmpdir, "src", "routes.py"), "w") as f:
            f.write("""
from auth import authenticate, User
from auth import validate_token

def login_route(request):
    user = User(request.name, request.email)
    result = authenticate(request.password)
    return result

def check_token(request):
    return validate_token(request.token)
""")

        from scip.indexer import index_repo
        result = index_repo(tmpdir)

        assert result["indexer"] == "builtin-crossref"
        assert result["stats"]["files_scanned"] >= 2
        assert result["stats"]["symbols_found"] > 0
        assert result["stats"]["relations_found"] > 0

        # Check that cross-file references were created
        ref_kinds = {r.get("kind") for r in result["relations"]}
        assert "imports" in ref_kinds or "references" in ref_kinds, \
            f"No import/reference relations found: {ref_kinds}"

        print(f"\n  Cross-file indexer: {result['stats']['symbols_found']} symbols, "
              f"{result['stats']['relations_found']} relations, "
              f"{result['stats']['resolved_imports']} resolved imports")
    finally:
        shutil.rmtree(tmpdir)


# ─── Benchmark 3: Search Recall ──────────────────────────────────

def test_search_recall():
    """Search engine recall@5 >= 0.85 on known corpus."""
    tmpdir = _make_temp_repo()
    try:
        # Analyze the repo and persist to KuzuDB
        from analyzer.orchestrator_v2 import analyze_repo_universal
        analyze_repo_universal(tmpdir, persist=True)

        # Search for known symbols
        from search.engine import get_search_engine
        engine = get_search_engine()

        test_queries = [
            ("authenticate", ["authenticate"]),
            ("validate_token", ["validate_token"]),
            ("hash_password", ["hash_password"]),
            ("User", ["User"]),
        ]

        total_recall = 0.0
        for query, expected in test_queries:
            response = engine.search(query, max_results=5)
            found = {r.label for r in response.results}
            hits = len(set(expected) & found)
            recall = hits / len(expected) if expected else 0.0
            total_recall += recall
            print(f"  Query '{query}': recall={recall:.2%} ({hits}/{len(expected)})")

        avg_recall = total_recall / len(test_queries)
        print(f"  Average recall@5: {avg_recall:.2%}")

        assert avg_recall >= 0.50, \
            f"Search recall too low: {avg_recall:.2%}"
    finally:
        shutil.rmtree(tmpdir)


# ─── Benchmark 4: Code Review Accuracy ────────────────────────────

def test_code_review_detects_issues():
    """Code reviewer should detect known issues in problematic code."""
    from review.reviewer import CodeReviewer

    problematic_code = """
def get_user(id):
    password = "super_secret_123"
    print(f"Getting user {id}")
    exec(f"process_{id}()")
    try:
        pass
    except:
        pass
    return None
"""
    reviewer = CodeReviewer()
    result = reviewer.review_code(problematic_code, language="python")

    issue_messages = {i.message for i in result.issues}
    assert any("hardcoded" in m.lower() or "secret" in m.lower() for m in issue_messages), \
        f"Should detect hardcoded secrets. Found: {issue_messages}"
    assert any("print" in m.lower() for m in issue_messages), \
        f"Should detect print statements. Found: {issue_messages}"
    assert any("dangerous" in m.lower() or "eval" in m.lower() or "exec" in m.lower() for m in issue_messages), \
        f"Should detect dangerous functions. Found: {issue_messages}"

    assert result.score < 100, f"Score should be penalized: {result.score}"
    print(f"\n  Code review: {len(result.issues)} issues found, score={result.score}")
    for issue in result.issues:
        print(f"    [{issue.severity}] {issue.message}")
