"""Canonical entry point for the enterprise knowledge benchmark suite.

Supported datasets only:
- FinanceBench (150 public questions)
- TAT-QA (default 500 sampled questions)
- CRUD-RAG (default 200 balanced Chinese cases)
- ViDoRe V3 (default 200 visual-document queries)
"""

from eval.benchmarks.suite import main


if __name__ == "__main__":
    main()
