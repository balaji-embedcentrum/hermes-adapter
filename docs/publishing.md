# Publishing to PyPI

The release workflow at `.github/workflows/release.yml` publishes to PyPI when a GitHub Release is created. Setup below is one-time.

## 1. Create the PyPI project (trusted publisher)

The recommended path uses [PyPI Trusted Publishers](https://docs.pypi.org/trusted-publishers/) — no long-lived tokens stored in GitHub secrets.

1. Log in to https://pypi.org/ with your account.
2. Go to **Your projects → Publishing → Add a new pending publisher**.
3. Fill in:
   - **PyPI Project Name:** `hermes-adapter`
   - **Owner:** `balaji-embedcentrum`
   - **Repository name:** `hermes-adapter`
   - **Workflow name:** `release.yml`
   - **Environment name:** `pypi`
4. Repeat at https://test.pypi.org/ with environment name `testpypi`.

## 2. Add GitHub Environments

In the repo settings (Settings → Environments), create two environments:

- `pypi` — production
- `testpypi` — pre-releases

No secrets needed. The `id-token: write` permission in `release.yml` lets GitHub issue an OIDC token that PyPI trusts.

## 3. Cut a release

```bash
# Bump version in both places (must match)
sed -i '' 's/version = "0.1.0"/version = "0.1.1"/' pyproject.toml
sed -i '' 's/__version__ = "0.1.0"/__version__ = "0.1.1"/' hermes_adapter/__init__.py

git commit -am "chore: release v0.1.1"
git tag v0.1.1
git push origin main --tags
```

Then go to **Releases → Draft a new release**, pick the tag, and publish. The workflow will:

- Build sdist + wheel
- Run `twine check`
- Upload to TestPyPI (if pre-release) or PyPI (otherwise)

## 4. Dry-run

Use the `workflow_dispatch` trigger with `dry_run=true` to build + check without uploading.

## 5. Manual fallback (API token)

If trusted publishing is unavailable:

1. Create a scoped API token at https://pypi.org/manage/account/token/.
2. Add it as a repo secret named `PYPI_API_TOKEN`.
3. Swap the publish step to:

```yaml
- uses: pypa/gh-action-pypi-publish@release/v1
  with:
    password: ${{ secrets.PYPI_API_TOKEN }}
```

Trusted publishers are preferred — short-lived OIDC tokens cannot leak if the repo is compromised.
