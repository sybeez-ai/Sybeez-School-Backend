# Sybeez Core API

```bash
cd apps/api
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export PYTHONPATH=.
uvicorn src.main:app --reload --port 8000
```
