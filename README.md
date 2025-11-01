# GitHub Metrics

Generate SVG cards that summarize a GitHub account's activity. The tool talks to the GitHub GraphQL API, rolls up repository statistics (stars, forks, language share, contribution totals), and renders the results into the `stats/` directory using the SVG templates that ship with the project. Environment variables are loaded automatically from a `.env` file via [`python-dotenv`](https://github.com/theskumar/python-dotenv).

## Highlighted features
- Collects stars, forks, repository counts, total contribution activity, and a language usage breakdown.
- Respects simple exclusion lists for repositories, languages, and forked projects.
- Renders polished SVG cards (`languages.svg`, `overview.svg`) that you can embed anywhere.
- Uses plain environment variables for configuration so it fits easily into CI or scheduled jobs.

## Installation (uv-first)
- Python 3.14 or newer (adjust `pyproject.toml` if you need an older interpreter).
- Install [uv](https://github.com/astral-sh/uv) (e.g., `pip install uv` or follow the official installer).

Sync dependencies and create a virtual environment managed by uv:

```bash
uv sync
```

uv creates a dedicated environment under `.venv/` by default. All subsequent commands can be run through uv without activating the environment manually.

> Need to stick with `pip`? Create a venv and run `pip install -e .` as usual.

## Configuration
Create a `.env` file in the project root (same directory as `pyproject.toml`). Variables are case-insensitive unless noted.

```bash
GITHUB_ACTOR=your-github-login
ACCESS_TOKEN=ghp_your_generated_token
# Optional tweaks:
# EXCLUDED_REPO=youruser/old-project,another-repo
# EXCLUDED_LANGS=html,css
# EXCLUDE_FORKED=false
# LANGS_LIMIT=8
```

| Variable | Required | Description |
| --- | --- | --- |
| `GITHUB_ACTOR` | ✅ | GitHub login (user or organization) whose metrics should be collected. |
| `ACCESS_TOKEN` | ✅ | Personal access token used for GraphQL calls. |
| `EXCLUDED_REPO` | ❌ | Comma-separated list of repository names or `owner/name` strings to skip. Matching is case-insensitive. |
| `EXCLUDED_LANGS` | ❌ | Comma-separated list of language names to ignore. |
| `EXCLUDE_FORKED` | ❌ | Boolean toggle (`1/0`, `true/false`, `yes/no`, defaults to `true`). Set to `false` to include forked repositories. |
| `LANGS_LIMIT` | ❌ | Maximum number of languages shown in the language card (default `10`). |

The renderer reads templates from `templates/` and writes finished SVGs to `stats/`. Both directories are created for you; customize the templates if you want to change colors, layout, or placeholders.

## Obtain a GitHub API token
1. Visit <https://github.com/settings/tokens> and click **Generate new token (classic)**.
2. Give the token a descriptive name (e.g., `github-metrics`).
3. Select the `public_repo` scope for public repositories; include the `repo` scope if you want private data or private language stats.
4. Generate the token and copy it into your `.env` as the value for `ACCESS_TOKEN`. GitHub will not show it again.

## Usage
After the `.env` file is populated, run the collector through uv:

```bash
uv run python -m github_metrics
```

On success you will find refreshed cards in `stats/languages.svg` and `stats/overview.svg`. Logs provide per-repository progress along with skipped items so you can confirm that exclusions behave as expected.

## Sample output
Below are the example cards generated from the bundled sample data:

![Overview card](stats/overview.svg)

![Languages card](stats/languages.svg)

## Tips & troubleshooting
- GitHub GraphQL rate limits are lower for unauthenticated requests; always provide a token.
- Contribution totals rely on the `contributionsCollection` field. If the token lacks permission to examine the target account, the total may come back as zero.
- To tweak layout or styling, edit the files under `templates/` and rerun the tool—the renderer performs a straight placeholder substitution, so changes are immediately reflected.
- `.env` values override the shell when both are present; drop the file if you want to rely solely on injected environment variables (e.g., in CI).
