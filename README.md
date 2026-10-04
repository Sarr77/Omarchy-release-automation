# Omarchy release automation

Publish prepared GitHub releases after their exact commits are verified in the
Omarchy plugin catalog.

The automation runs in this repository. Plugin repositories keep their source
commits and verified snapshots unchanged. It publishes only the release title,
notes and attachments approved by the owner in advance.

ScratchPeek and WindowPeek have separate publication jobs, plans, concurrency
groups and repository-scoped release tokens. A shared plan token can only read
and clear authorization variables in this automation repository. Without a plan,
that project's job is skipped before a runner starts.

See [setup and release preparation](docs/RELEASE-AUTOMATION.md).

## Checks

```sh
python3 -B -m unittest discover -s tests -v
```

MIT licensed.
