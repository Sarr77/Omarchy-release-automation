# Omarchy release automation

Publish prepared GitHub releases after their exact commits are verified in the
Omarchy plugin catalog.

The automation runs in this repository. Plugin repositories keep their source
commits and verified snapshots unchanged. It publishes only the release title,
notes and attachments approved by the owner in advance.

ScratchPeek is the only configured project. The project configuration and
publisher are separate so other plugins can be added later with their own
release plans and repository-scoped tokens.

See [setup and release preparation](docs/RELEASE-AUTOMATION.md).

## Checks

```sh
python3 -B -m unittest discover -s tests -v
```

MIT licensed.
