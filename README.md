# GitHub Metrics

Generate SVG cards that summarize a GitHub account's activity. The tool talks to the GitHub GraphQL API, rolls up repository statistics (stars, forks, language share, contribution totals), and renders the results into the `stats/` directory using the SVG templates that ship with the project.

## Highlighted features
- Collects stars, forks, repository counts, total contribution activity, and a language usage breakdown.
- Respects simple exclusion lists for repositories, languages, and forked projects.
- Renders polished SVG cards (`languages.svg`, `overview.svg`) that you can embed anywhere.
- Uses plain environment variables for configuration so it fits easily into CI or scheduled jobs.

## Requirements
- Python 3.14 or newer (adjust `pyproject.toml` if you need an older interpreter).
- A GitHub personal access token with enough scope to read the repositories you want to include (`public_repo` is sufficient for public data; add `repo` to include private repos).

Install dependencies after creating a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

If you prefer [uv](https://github.com/astral-sh/uv), you can run `uv pip install -e .` inside your environment instead.

## Configuration
Set the variables below before running the collector. Values are case-insensitive unless noted.

| Variable | Required | Description |
| --- | --- | --- |
| `GITHUB_ACTOR` | ✅ | GitHub login (user or organization) whose metrics should be collected. |
| `ACCESS_TOKEN` | ✅ | Personal access token used for GraphQL calls. |
| `EXCLUDED_REPO` | ❌ | Comma-separated list of repository names or `owner/name` strings to skip. Matching is case-insensitive. |
| `EXCLUDED_LANGS` | ❌ | Comma-separated list of language names to ignore. |
| `EXCLUDE_FORKED` | ❌ | Boolean toggle (`1/0`, `true/false`, `yes/no`, defaults to `true`). Set to `false` to include forked repositories. |
| `LANGS_LIMIT` | ❌ | Maximum number of languages shown in the language card (default `10`). |

The renderer reads templates from `templates/` and writes finished SVGs to `stats/`. Both directories are created for you; customize the templates if you want to change colors, layout, or placeholders.

## Usage
With your environment configured, run:

```bash
python -m github_metrics
```

On success you will find refreshed cards in `stats/languages.svg` and `stats/overview.svg`. Logs provide per-repository progress along with skipped items so you can confirm that exclusions behave as expected.

## Tips & troubleshooting
- GitHub GraphQL rate limits are lower for unauthenticated requests; always provide a token.
- Contribution totals rely on the `contributionsCollection` field. If the token lacks permission to examine the target account, the total may come back as zero.
- To tweak layout or styling, edit the files under `templates/` and rerun the tool—the renderer performs a straight placeholder substitution, so changes are immediately reflected.

