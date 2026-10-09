# Runbook

## Track H — coding-agent harness

```bash
python -m venv ~/venvs/harness && ~/venvs/harness/bin/pip install openai
export BASE_URL=https://opencode.ai/zen/v1 API_KEY=<key> MODEL=<model-id>
export AGENT_WORKSPACE=/workspaces/final-year-project   # sandbox root
cd harness && python main.py
```

REPL: `/new /sessions /resume <id> /title /memory /reflect /exit`.
State files (`sessions.db`, `MEMORY.md`, `todo.md`) live in the cwd.

## Track B — GraphRAG indexer

```bash
cd graphrag && docker compose --env-file ../.env up -d     # never `source ../.env`
~/venvs/graphrag/bin/python -m graphrag.wikipedia "Title" --out ../data/corpus_test
~/venvs/graphrag/bin/python -m graphrag.pipeline --corpus-dir ../data/corpus_test --batch 4 --workers 4
~/venvs/graphrag/bin/python -m unittest discover -s graphrag/tests
```

## Environment lessons (measured)

- `source .env` in a shell spews laya-serve output and hangs the command.
  Use `docker compose --env-file ../.env` instead.
- `~/venvs/graphrag` python has **no SQLite FTS5**; system python and
  `~/venvs/laya-server` python do. Harness session search needs FTS5.
- Wikipedia fetch 429s after ~11 rapid articles; space fetches minutes apart.
