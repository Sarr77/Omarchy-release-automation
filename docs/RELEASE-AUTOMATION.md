# Release publication after marketplace verification

The workflow runs in `Sarr77/Omarchy-release-automation`. Its only configured
release destination is `Sarr77/ScratchPeek`. Nothing is pushed to the plugin's
branches: adding or updating this automation does not change the plugin's HEAD
or create a different upstream commit for the marketplace to detect.

It publishes one prepared draft after the official catalog verifies that
exact source commit. It checks every 15 minutes, at minutes 7, 22, 37 and 52 UTC.
GitHub can delay or drop runs; this is not a guaranteed publication deadline.
Manual runs default to check-only. It uses Python's standard library and the
GitHub REST API on a standard public Ubuntu runner, without external workers,
models, paid runners, Actions caches or uploaded Actions artifacts.

## One-time setup

Setup and publication require the owner's approval of the exact final files,
commit, destination, repository visibility and release plan.

1. Create the public automation repository with this reviewed commit on `main`.
   Leave the plugin repository's `main` and settings unchanged. Its immutable
   releases setting must already be enabled; the publisher reads but never
   changes it.
2. Create two fine-grained personal access tokens owned by Sarr77. Restrict
   each token to only the repository listed below. Store both as Actions
   secrets in the automation repository:

   | Secret | Only selected repository | Permissions |
   | --- | --- | --- |
   | `SCRATCHPEEK_RELEASE_TOKEN` | `Sarr77/ScratchPeek` | Contents read/write; Administration read |
   | `RELEASE_PLAN_TOKEN` | `Sarr77/Omarchy-release-automation` | Variables read/write |

   GitHub includes Metadata read automatically. Contents write publishes the
   draft; Administration read checks immutable releases; Variables read/write
   rechecks and removes the authorization. No Secrets, Actions, Issues or Pull
   requests permission is needed for the running publisher. The release token
   does not need Variables access; the plan token cannot change plugin code or
   publish a release. The workflow's built-in `GITHUB_TOKEN` has only Contents
   read and is used by checkout without persisting credentials.

   **Workflows write is conditional.** GitHub requires it when a release's
   resolved target adds or modifies `.github/workflows/` relative to the
   plugin repository's default branch. For the prepared 0.11.2 release, the
   target and ScratchPeek `main` both remain `f8e9754f8804db852407f804f36cb0b69c2e82c4`,
   so that additional permission is not needed. If a future release has such
   a difference, review and approve adding Workflows read/write to that
   project's release token, or wait for the target to match the default branch.
   Never broaden the token to all repositories. The built-in workflow token
   cannot be authorized for Workflows write.

3. Renew tokens before expiry. Never put them in source, plan variables, notes
   or logs, and do not share them in a conversation. GitHub cannot limit
   Contents write to a single release operation: the release token has broader
   capability within the selected plugin repository. The publisher's own
   allowlist permits only publication of a prepared release, without source,
   tag or attachment writes. Keep write access to the automation's `main` and
   Actions settings limited to trusted maintainers. Tests run before the step
   with secrets; pull request tests have no publication secrets.
4. After configuring an approved plan, run the workflow in check-only mode and
   inspect its result. Local tests and actionlint do not prove a hosted run or
   the permissions of newly issued tokens.

No plan means the publication job is skipped without starting a runner. A fork,
private automation repository or run on a branch other than `main` is skipped.
A skipped workflow record remains in Actions. An armed plan requests up to 96
checks a day. The two stored tokens alone do not authorize any release.

## Preparing each release

Marketplace verification authorizes a source snapshot; it does not approve
release text. The owner must separately approve the full SHA, release title,
full notes, all attachments and their hashes, destination and deferred
publication after catalog verification.

1. Build the reproducible plugin archive from that exact commit, rather than
   a working directory with later changes. Review the complete materials.
2. Prepare or reuse a matching draft in the plugin repository, with the full
   SHA as `target_commitish`, a `vX.Y.Z` tag, approved title and notes, no
   prerelease and no discussion. Attach the reviewed ZIP and optional checksum.
   Prepare it as Sarr77. Create the tag at that exact commit before arming the
   job. Creating a tag is a public change and also needs approval. Never move
   an existing tag or replace an immutable release.
3. Generate the plan locally from the standalone automation checkout:

   ```sh
   python3 -B tools/prepare_release_plan.py \
     --project scratchpeek \
     --checkout /path/to/ScratchPeek \
     --commit FULL_COMMIT_SHA \
     --archive /path/to/ScratchPeek-X.Y.Z.zip \
     --checksum /path/to/ScratchPeek-X.Y.Z.sha256 \
     --title 'ScratchPeek X.Y.Z' \
     --notes /path/to/release-notes.md \
     --release-id DRAFT_ID \
     --approve-for-publication \
     --output /path/to/release-plan.json
   ```

   This is offline and checks every archived file against the package file
   list and Git blobs at the exact plugin commit. Symlinks, non-commit objects,
   additional files and invalid checksum contents are rejected. Omitting the
   approval flag creates a disarmed review template. The flag does not grant
   owner consent by itself. The plan must fit GitHub's 48 KiB variable limit.
4. Put the entire approved JSON into the **automation repository's** Actions
   variable `SCRATCHPEEK_RELEASE_PLAN`. Do not put it in ScratchPeek, and do
   not commit the plan or future release text to either repository. This arms
   exactly that draft. Only one plan per project may be armed. Do not replace
   it while a publication job is running. Clear it, cancel the job and wait
   for completion before replacing it; clearing cannot recall a publication
   request already sent to GitHub.
5. Submit the plugin commit for marketplace verification as usual. Once the
   catalog confirms that SHA, the next successful scheduled check publishes
   the already approved draft. No author session is needed at that point.

The publisher does not generate text, build packages, create drafts, select
HEAD, upload attachments, create or move tags, change repo settings or push
source commits. The released source is the plugin SHA in the plan, not this
repository's automation commit. If 0.11.2 is published manually first, do not
arm its plan; reserve the workflow for the next prepared release.

## Checks and recovery

The publisher checks the configured public plugin repository, source manifest,
resolved tag, draft ID, author, full title and notes, and all attachment names,
labels, sizes and GitHub-provided SHA-256 digests. Missing digests block it.
The version must be newer than the latest stable release. The catalog must
contain an installable verified snapshot with the exact same commit. The
catalog policy is derived from the plugin's existing updater rules; no updater
or plugin runtime is imported or executed.

Immediately before publication it rechecks draft contents, tag, immutability,
latest release and catalog, then rereads the plan from the automation repository.
The release token is sent only to the plugin's API paths; the plan token only
to the automation repository's configured plan endpoints. Cross-repository
requests and branch or tag mutation operations are refused by the client.

The job verifies the published release, exact tag, immutable state, approved
text and attachments, and latest marker before clearing the same plan. These
APIs do not offer a cross-service transaction or conditional compare-and-delete.
A change after the final check cannot be excluded. Do not edit the draft, tag
or plan during publication; cancel and wait before making a replacement.

- `waiting-for-marketplace`: no eligible snapshot for that SHA; no writes.
- `ready`: a check-only run passed; no writes.
- `published`: the draft is published and verified; the completed plan is cleared.
- `already-published`: a retry found that immutable release. It does not publish
  again, overwrite later text corrections or demote a newer latest release.
  Publication mode clears only the still matching completed plan.
- `blocked`: mismatch, revoked consent, expired token, unavailable service or
  unsupported metadata stops the job. An error after publication does not
  delete or roll back the release. Inspect the result before retrying.

An immutable release's title and notes can still be edited, but each correction
needs approval of its exact final text. Tags and attachments remain immutable.

GitHub disables public scheduled workflows after 60 days of inactivity in the
**automation repository**; plugin commits do not reset that repository's timer.
Check the workflow is enabled and tokens valid when arming a new release.
Failures appear in Actions; the publisher posts no messages or issues.

## Adding another project later

`tools/projects.py` is the explicit allowlist of projects and destinations.
The publisher, catalog checker and offline plan tool receive a project
configuration; the plan cannot supply a different destination. ScratchPeek is
the only enabled entry. WindowPeek has no configuration, token, workflow job
or active plan in this version.

To add a project, review its repository, plugin ID, package layout, archive
prefix and author, then add a configuration and a separate workflow job. Give
that job its own per-project concurrency group, variable and release token,
and expose only that project's token to it. A separate named job can be skipped
when its own plan is empty, without consuming a runner. This avoids coupling
WindowPeek's approval, release version and failures to ScratchPeek's.
The plan token can be shared because it can read/write only variables in this
automation repository; it has no plugin repository access. Extending the
allowlist or enabling another job requires a new exact review and approval.
Neither plugin repository needs an automation commit.

References: [standard public runners](https://docs.github.com/en/billing/concepts/product-billing/github-actions),
[schedules](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
[release permissions](https://docs.github.com/en/rest/releases/releases#update-a-release),
[variable permissions](https://docs.github.com/en/rest/actions/variables#delete-a-repository-variable),
[immutability setting](https://docs.github.com/en/rest/repos/repos#check-if-immutable-releases-are-enabled-for-a-repository).
