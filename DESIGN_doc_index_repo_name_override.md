# doc_index_repo Name Override Design

## Goal

Add the remaining citeable-index follow-up from PR #17: let callers choose a
safe stored index name when indexing a GitHub repository with `doc_index_repo`.

This is for systems like Doc Relay that need multiple logical indexes for the
same upstream repository, usually one per library version, while preserving the
original GitHub source identity for freshness and provenance.

## Non-Goals

- Multiple aliases pointing to one index.
- Moving aliases such as `latest`.
- Branch, tag, or ref handles as repo identifiers.
- Historical snapshot retention or checkout.
- Repo group changes.
- Arbitrary alias strings.
- Accepting `/`, `\\`, or `@` inside storage names.
- Changing `index_local(name=...)`.
- Changing Doc Relay in this branch.

## API Shape

Add optional `name` to `doc_index_repo` / `index_repo`:

```python
doc_index_repo(
    url="https://github.com/facebook/react",
    name="github__facebook__react__18.2.0",
    incremental=True,
    use_embeddings="auto",
    use_ai_summaries=False,
)
```

Behavior:

- If `name` is omitted, preserve current behavior: store as `owner/repo`.
- If `name` is provided, store as `owner/name`.
- `name` is a storage-name override, not an alias pointer.
- `name` must obey the same safe component rule as stored repo names:
  `[A-Za-z0-9._-]+`.

## Why `@` Is Not Allowed In Names

The repo-at-SHA feature uses strict handles in this form:

```text
owner/repo@40hexsha
```

Allowing `@` in the stored index name would make strict handle parsing harder
to reason about, especially when a caller's version marker is itself a full
40-character SHA. Keep jdocmunch storage names filesystem-safe and
repo-at-SHA-safe.

Doc Relay can keep its own canonical row unchanged and map it to a jdoc-safe
name before calling jdocmunch:

```text
Doc Relay canonical: github__facebook__react@18.2.0
jdoc index name:     github__facebook__react__18.2.0
```

## Identity Fields

With a name override, there are two identities:

- Stored jdoc index identity: `facebook/github__facebook__react__18.2.0`
- Upstream source identity: `facebook/react`

`doc_index_repo` should preserve both. Suggested response shape:

```json
{
  "success": true,
  "repo": "facebook/github__facebook__react__18.2.0",
  "source_repo": "facebook/react",
  "head_sha": "40hex...",
  "source_dirty": false,
  "sha_certified": true,
  "repo_at_sha": "facebook/github__facebook__react__18.2.0@40hex...",
  "source_repo_at_sha": "facebook/react@40hex..."
}
```

Meaning:

- `repo` is the stored index handle used for `search_sections`,
  `get_doc_health`, `get_index_overview`, and `delete_index`.
- `repo_at_sha` is the stored corpus citation.
- `source_repo` is the original GitHub source repository.
- `source_repo_at_sha` is the upstream source snapshot citation.

## Implementation Plan

1. Add `name: Optional[str] = None` to `index_repo()`.
2. After `parse_github_url(url)`, compute:

   ```python
   source_repo = repo
   index_name = name if name is not None else repo
   repo_id = f"{owner}/{index_name}"
   source_repo_id = f"{owner}/{source_repo}"
   ```

3. Validate explicit `name` before using it:

   - reject blank strings
   - reject `/`, `\\`, and `@`
   - reject characters outside `[A-Za-z0-9._-]+`
   - return a structured error, not a traceback

4. Replace storage calls to use `(owner, index_name)`:

   - `store.load_index(owner, repo)` -> `store.load_index(owner, index_name)`
   - `store.detect_changes(owner, repo, ...)` -> `store.detect_changes(owner, index_name, ...)`
   - `store.incremental_save(owner=owner, name=repo, ...)` -> `name=index_name`
   - `store.save_index(owner=owner, name=repo, ...)` -> `name=index_name`

5. Parse sections under the stored repo id:

   ```python
   parse_file(content, path, repo_id)
   ```

6. Return `repo: repo_id`, not `source_repo_id`.
7. Add source metadata to successful responses:

   - `source_repo`
   - `source_repo_at_sha` when `head_sha` is certified

8. Update MCP schema for `doc_index_repo` with the optional `name` property.
9. Update MCP dispatch to pass `name=arguments.get("name")`.
10. Update `SPEC.md` after behavior and tests are in place.

## Tests

Add coverage for:

- `doc_index_repo` without `name` preserves current `owner/repo` behavior.
- `doc_index_repo(name="custom_docs")` stores under `owner/custom_docs`.
- Result includes `repo == "owner/custom_docs"`.
- Result includes `source_repo == "owner/original"`.
- Certified result includes:
  - `repo_at_sha == "owner/custom_docs@sha"`
  - `source_repo_at_sha == "owner/original@sha"`
- Incremental fast path uses the custom name, not the source repo name.
- Changed-file incremental path uses the custom name.
- `doc_list_repos` shows `owner/custom_docs`.
- `search_sections(repo="owner/custom_docs", ...)` works.
- Strict `search_sections(repo="owner/custom_docs@sha", ...)` works.
- Invalid names fail cleanly:
  - `name="foo@bar"`
  - `name="foo/bar"`
  - `name=""`
  - `name=".."`
