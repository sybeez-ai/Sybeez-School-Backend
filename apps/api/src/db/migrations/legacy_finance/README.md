These are the finance-branch revisions that production already applied under ids `0028`, `0029`, and `0030`. They are not on the Alembic path. The live chain uses `0041`–`0043` for the same SQL, and `0028`–`0030` for HRM.

`tests/integration/test_legacy_finance_stamp.py` replays them to build a database shaped like RDS `sybeez-school-db` at the old finance `0030`.
