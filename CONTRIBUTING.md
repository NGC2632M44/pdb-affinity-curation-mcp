# Contributing

## Before a pull request

```powershell
cd mcp/pdb-curation-mcp
python -m py_compile server.py tests/test_server.py
python server.py --self-test
python -m unittest discover tests -v
```

## Data policy

Do not submit:

- paywalled papers or supplements;
- Reaxys, SciFinder or CAS exports;
- human-review workbooks;
- credentials or local configuration;
- patient, participant or confidential institutional data.

Use synthetic fixtures or small public-domain examples in tests.

## Schema changes

Keep producer, validator, docs and tests aligned. A new missing reason must
include its allowed fields, default status and evidence requirement. A new
ledger column must include migration behavior and a regression test.

