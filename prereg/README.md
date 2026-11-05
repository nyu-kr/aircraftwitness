# Registered plans

One folder per test in the paper's Table 4, plus `e2e` and `fresh`, the two plans of 5 November, and `dataset_split`, the rules for the dataset's public squares. `INDEX.tsv` gives each file's sha256, test, dated commit or log entry and Bitcoin block.

- `plan.md`, `rule.md`: the plan as last amended.
- `*_stamped.md`, `plan_HHMM.md`, `scoring_rule.md`, `COMMITS.tsv`, `*_rule.md`: the exact bytes timestamped.
- `amendment_*.md`: dated amendments, each timestamped before the runs it affects.
- `*.ots`: OpenTimestamps receipts. A receipt alone covers sealed squares and proves only that their hash existed then.

Check a stamp with `ots verify FILE.ots` and a Bitcoin node, or with `ots --no-bitcoin verify FILE.ots` and the printed Merkle root on any block explorer.

Timestamped files are byte exact and keep their original wording, including internal role names for the verifier and workers. Other files read NETID for the computing account's user name, name no development site, and use plain roles (the coordinator, the verifier, a worker) for internal names. `park/rule.md`, `xc_later/plan.md` and `cleaner/plan.md` are cleaned copies of timestamped files that did either. A receipt would not verify on them, so none ships.
