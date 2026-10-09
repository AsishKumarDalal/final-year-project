"""Unit tests for Track B.

Stdlib ``unittest`` only — no pytest fixture, no network, no API key. These run
before a single token is ever spent, because chunking, normalisation, the
validation gate and aggregation are all pure functions.

Run:  python3 -m unittest discover -s graphrag/tests -t .
"""