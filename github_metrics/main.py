from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
GRAPHQL_URL = "https://api.github.com/graphql"
DEFAULT_LANGUAGE_COLOR = "#ededed"


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


class GitHubAPIError(Exception):
    """Raised when the GitHub API returns an error response."""


@dataclass
class Config:
    login: str
    token: str
    excluded_repos: set[str]
    excluded_languages: set[str]
    exclude_forked: bool
    languages_limit: int
    templates_dir: Path
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        login = os.getenv("GITHUB_ACTOR")
        if not login:
            raise ConfigError("Environment variable GITHUB_ACTOR is required.")

        token = os.getenv("ACCESS_TOKEN")
        if not token:
            raise ConfigError("Environment variable ACCESS_TOKEN is required.")

        excluded_repos = parse_csv(os.getenv("EXCLUDED_REPO"))
        excluded_languages = parse_csv(os.getenv("EXCLUDED_LANGS"))
        exclude_forked = str_to_bool(os.getenv("EXCLUDE_FORKED"), default=True)

        langs_limit_raw = os.getenv("LANGS_LIMIT")
        if langs_limit_raw:
            try:
                languages_limit = int(langs_limit_raw)
            except ValueError as exc:
                raise ConfigError("LANGS_LIMIT must be an integer.") from exc
            if languages_limit <= 0:
                raise ConfigError("LANGS_LIMIT must be greater than zero.")
        else:
            languages_limit = 10

        base_dir = Path(__file__).resolve().parent.parent
        templates_dir = base_dir / "templates"
        output_dir = base_dir / "stats"

        if not templates_dir.exists():
            raise ConfigError(f"Templates directory not found: {templates_dir}")

        return cls(
            login=login,
            token=token,
            excluded_repos=excluded_repos,
            excluded_languages=excluded_languages,
            exclude_forked=exclude_forked,
            languages_limit=languages_limit,
            templates_dir=templates_dir,
            output_dir=output_dir,
        )


@dataclass
class LanguageShare:
    name: str
    size: int
    color: str
    percent: float


@dataclass
class MetricsResult:
    actor_login: str
    display_name: str
    total_stars: int
    total_forks: int
    total_contributions: int
    total_lines_changed: int
    total_views: int
    repository_count: int
    languages: List[LanguageShare]


class GraphQLClient:
    def __init__(self, token: str, timeout: int = 30) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        self.timeout = timeout

    def execute(self, query: str, variables: Dict[str, object]) -> Dict[str, object]:
        response = self.session.post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables},
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise GitHubAPIError(
                f"GitHub API HTTP error: {exc} - {response.text}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubAPIError("GitHub API returned invalid JSON.") from exc

        errors = payload.get("errors") or []
        if errors:
            messages = ", ".join(err.get("message", "unknown error") for err in errors)
            raise GitHubAPIError(f"GitHub API error: {messages}")

        data = payload.get("data")
        if data is None:
            raise GitHubAPIError("GitHub API response missing data field.")

        return data


class StatsCollector:
    _REPOSITORIES_QUERY = """
    query($login: String!, $cursor: String, $isFork: Boolean) {
      repositoryOwner(login: $login) {
        login
        ... on User {
          name
        }
        ... on Organization {
          name
        }
        repositories(
          first: 50,
          after: $cursor,
          isFork: $isFork,
          orderBy: { field: STARGAZERS, direction: DESC }
        ) {
          nodes {
            name
            nameWithOwner
            stargazerCount
            forkCount
            isFork
            languages(first: 100, orderBy: { field: SIZE, direction: DESC }) {
              totalSize
              pageInfo {
                hasNextPage
                endCursor
              }
              edges {
                size
                node {
                  name
                  color
                }
              }
            }
          }
          pageInfo {
            hasNextPage
            endCursor
          }
        }
      }
    }
    """

    _LANGUAGES_PAGE_QUERY = """
    query($owner: String!, $name: String!, $cursor: String) {
      repository(owner: $owner, name: $name) {
        languages(first: 100, after: $cursor, orderBy: { field: SIZE, direction: DESC }) {
          pageInfo {
            hasNextPage
            endCursor
          }
          edges {
            size
            node {
              name
              color
            }
          }
        }
      }
    }
    """

    _CONTRIBUTIONS_QUERY = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            totalContributions
          }
        }
      }
    }
    """

    def __init__(self, config: Config, client: GraphQLClient) -> None:
        self.config = config
        self.client = client

    def run(self) -> MetricsResult:
        owner_name: Optional[str] = None
        total_stars = 0
        total_forks = 0
        total_lines = 0
        repository_count = 0
        language_totals: Dict[str, Dict[str, object]] = {}

        cursor: Optional[str] = None
        is_fork_filter: Optional[bool] = False if self.config.exclude_forked else None

        while True:
            data = self.client.execute(
                self._REPOSITORIES_QUERY,
                {
                    "login": self.config.login,
                    "cursor": cursor,
                    "isFork": is_fork_filter,
                },
            )

            owner = data.get("repositoryOwner")
            if owner is None:
                raise ConfigError(
                    f"GitHub owner '{self.config.login}' was not found or is not accessible."
                )

            if owner_name is None:
                owner_name = (
                    owner.get("name") or owner.get("login") or self.config.login
                )

            repositories = owner.get("repositories") or {}
            nodes = repositories.get("nodes") or []

            for node in nodes:
                repo_name = node.get("name")
                name_with_owner = node.get("nameWithOwner") or repo_name
                if not repo_name:
                    continue

                lower_repo_names = {
                    repo_name.lower(),
                    (name_with_owner or "").lower(),
                }
                if self.config.excluded_repos & lower_repo_names:
                    logger.info("⏭️ Skipping repository %s (excluded).", name_with_owner)
                    continue

                languages_block = node.get("languages") or {}
                edges = list(languages_block.get("edges") or [])

                page_info = languages_block.get("pageInfo") or {}
                has_next = page_info.get("hasNextPage")
                end_cursor = page_info.get("endCursor")

                if has_next and name_with_owner:
                    owner_part = (
                        name_with_owner.split("/")[0]
                        if "/" in name_with_owner
                        else self.config.login
                    )
                    edges.extend(
                        self._fetch_additional_languages(
                            owner_part, repo_name, end_cursor
                        )
                    )

                filtered_languages: List[LanguageShare] = []
                for edge in edges:
                    lang_node = edge.get("node") or {}
                    lang_name = lang_node.get("name")
                    if not lang_name:
                        continue
                    if lang_name.lower() in self.config.excluded_languages:
                        continue

                    size_value = edge.get("size") or 0
                    try:
                        size = int(size_value)
                    except (TypeError, ValueError):
                        continue
                    if size <= 0:
                        continue

                    color = lang_node.get("color") or DEFAULT_LANGUAGE_COLOR
                    filtered_languages.append(
                        LanguageShare(
                            name=lang_name,
                            size=size,
                            color=color,
                            percent=0.0,  # placeholder, updated later
                        )
                    )

                    total_lines += size
                    bucket = language_totals.setdefault(
                        lang_name,
                        {"size": 0, "color": color or DEFAULT_LANGUAGE_COLOR},
                    )
                    bucket["size"] = int(bucket.get("size", 0)) + size
                    if not bucket.get("color") and color:
                        bucket["color"] = color

                stars = int(node.get("stargazerCount") or 0)
                forks = int(node.get("forkCount") or 0)
                total_stars += stars
                total_forks += forks

                repository_count += 1

                language_summary = (
                    ", ".join(
                        f"{item.name}: {format_number(item.size)}"
                        for item in filtered_languages
                    )
                    if filtered_languages
                    else "no tracked languages"
                )
                logger.info(
                    "Repo %s — ⭐ stars=%s 🍴 forks=%s 💻 languages=%s",
                    name_with_owner,
                    format_number(stars),
                    format_number(forks),
                    language_summary,
                )

            page_info = repositories.get("pageInfo") or {}
            if page_info.get("hasNextPage"):
                cursor = page_info.get("endCursor")
            else:
                break

        if owner_name is None:
            owner_name = self.config.login

        total_contributions = self._fetch_contributions_total()
        total_views = 0  # Views are not available via GraphQL without elevated scopes.

        languages = self._build_language_shares(language_totals, total_lines)

        return MetricsResult(
            actor_login=self.config.login,
            display_name=owner_name,
            total_stars=total_stars,
            total_forks=total_forks,
            total_contributions=total_contributions,
            total_lines_changed=total_lines,
            total_views=total_views,
            repository_count=repository_count,
            languages=languages,
        )

    def _fetch_additional_languages(
        self, owner: str, name: str, cursor: Optional[str]
    ) -> List[Dict[str, object]]:
        edges: List[Dict[str, object]] = []
        next_cursor = cursor
        while next_cursor:
            data = self.client.execute(
                self._LANGUAGES_PAGE_QUERY,
                {"owner": owner, "name": name, "cursor": next_cursor},
            )
            repository = data.get("repository") or {}
            languages = repository.get("languages") or {}
            edges.extend(languages.get("edges") or [])
            page_info = languages.get("pageInfo") or {}
            if page_info.get("hasNextPage"):
                next_cursor = page_info.get("endCursor")
            else:
                next_cursor = None
        return edges

    def _fetch_contributions_total(self) -> int:
        try:
            data = self.client.execute(
                self._CONTRIBUTIONS_QUERY,
                {"login": self.config.login},
            )
        except GitHubAPIError as exc:
            logger.warning("⚠️ Failed to fetch contributions: %s", exc)
            return 0

        user = data.get("user")
        if not user:
            return 0
        collection = user.get("contributionsCollection") or {}
        calendar = collection.get("contributionCalendar") or {}
        total = calendar.get("totalContributions") or 0
        try:
            return int(total)
        except (TypeError, ValueError):
            return 0

    def _build_language_shares(
        self,
        language_totals: Dict[str, Dict[str, object]],
        total_lines: int,
    ) -> List[LanguageShare]:
        if total_lines <= 0 or not language_totals:
            return []

        sorted_items = sorted(
            language_totals.items(),
            key=lambda item: int(item[1].get("size", 0)),
            reverse=True,
        )

        languages: List[LanguageShare] = []
        for index, (name, data) in enumerate(sorted_items):
            if index >= self.config.languages_limit:
                break
            size = int(data.get("size", 0))
            color = (data.get("color") or DEFAULT_LANGUAGE_COLOR)[:7]
            percent = (size / total_lines) * 100 if total_lines else 0.0
            languages.append(
                LanguageShare(
                    name=name,
                    size=size,
                    color=color,
                    percent=percent,
                )
            )

        return languages


class TemplateRenderer:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.config.output_dir.mkdir(parents=True, exist_ok=True)

    def render_languages(self, metrics: MetricsResult) -> None:
        template_path = self.config.templates_dir / "languages.svg"
        template = template_path.read_text(encoding="utf-8")

        progress_markup = self._build_progress_markup(metrics.languages)
        lang_list_markup = self._build_lang_list(metrics.languages)

        output = template.replace("{{ progress }}", progress_markup).replace(
            "{{ lang_list }}", lang_list_markup
        )

        output_path = self.config.output_dir / "languages.svg"
        output_path.write_text(output, encoding="utf-8")
        logger.info("💾 Wrote %s", output_path)

    def render_overview(self, metrics: MetricsResult) -> None:
        template_path = self.config.templates_dir / "overview.svg"
        template = template_path.read_text(encoding="utf-8")

        replacements = {
            "{{ name }}": metrics.display_name,
            "{{ stars }}": format_number(metrics.total_stars),
            "{{ forks }}": format_number(metrics.total_forks),
            "{{ contributions }}": format_number(metrics.total_contributions),
            "{{ lines_changed }}": format_number(metrics.total_lines_changed),
            "{{ views }}": format_number(metrics.total_views),
            "{{ repos }}": format_number(metrics.repository_count),
        }

        output = template
        for placeholder, value in replacements.items():
            output = output.replace(placeholder, value)

        output_path = self.config.output_dir / "overview.svg"
        output_path.write_text(output, encoding="utf-8")
        logger.info("💾 Wrote %s", output_path)

    def _build_progress_markup(self, languages: List[LanguageShare]) -> str:
        if not languages:
            return '<span class="progress-item" style="background-color: #ededed; width: 100%;"></span>'

        segments = []
        for lang in languages:
            width = max(lang.percent, 0.0)
            segments.append(
                f'<span class="progress-item" style="background-color: {lang.color}; width: {width:.2f}%;"></span>'
            )
        return "".join(segments)

    def _build_lang_list(self, languages: List[LanguageShare]) -> str:
        if not languages:
            return '<li style="animation-delay: 0ms"><span class="lang">No tracked languages</span><span class="percent">0%</span></li>'

        items = []
        for index, lang in enumerate(languages):
            delay_ms = index * 120
            items.append(
                f'<li style="animation-delay: {delay_ms}ms"><span class="lang">{lang.name}</span><span class="percent">{lang.percent:.2f}%</span></li>'
            )
        return "\n".join(items)


def parse_csv(raw: Optional[str]) -> set[str]:
    if not raw:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def str_to_bool(value: Optional[str], default: bool = True) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"Invalid boolean value: {value}")


def format_number(value: int) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    load_dotenv()
    try:
        config = Config.from_env()
    except ConfigError as exc:
        logger.error("❌ Configuration error: %s", exc)
        sys.exit(1)

    client = GraphQLClient(config.token)
    collector = StatsCollector(config, client)

    try:
        metrics = collector.run()
    except (ConfigError, GitHubAPIError) as exc:
        logger.error("❌ Failed to collect GitHub metrics: %s", exc)
        sys.exit(1)

    renderer = TemplateRenderer(config)
    renderer.render_languages(metrics)
    renderer.render_overview(metrics)

    # Final summary message with all collected statistics
    logger.info("\n📊 Final GitHub Statistics Summary:")
    logger.info("👤 User: %s", metrics.display_name)
    logger.info("⭐ Total Stars: %s", format_number(metrics.total_stars))
    logger.info("🍴 Total Forks: %s", format_number(metrics.total_forks))
    logger.info("📈 Total Contributions: %s", format_number(metrics.total_contributions))
    logger.info("💻 Total Lines Changed: %s", format_number(metrics.total_lines_changed))
    logger.info("👀 Total Repository Views: %s", format_number(metrics.total_views))
    logger.info("📦 Total Repositories: %s", format_number(metrics.repository_count))
    logger.info("🛠️ Top Languages:")
    for i, lang in enumerate(metrics.languages[:5], 1):  # Show top 5 languages
        logger.info("   %d. %s (%.2f%%)", i, lang.name, lang.percent)
    logger.info("✅ GitHub metrics collection completed successfully!")


if __name__ == "__main__":
    main()
