package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"sort"
	"strconv"
	"strings"
	"text/template"
	"time"

	"github.com/joho/godotenv"
	"github.com/shurcooL/githubv4"
	"golang.org/x/oauth2"
)

type LanguageStat struct {
	Name       string
	Color      string
	Size       int
	Percentage float64
}

type TemplateData struct {
	Name      string
	Languages []LanguageStat
}

type OverviewStats struct {
	Name          string
	Stars         int
	Forks         int
	Repos         int
	Contributions string
	LinesChanged  string
	Views         string
}

type Config struct {
	GitHubActor   string
	AccessToken   string
	ExcludedRepos map[string]bool
	ExcludedLangs map[string]bool
	ExcludeForked bool
	LangsLimit    int
}

// Repository GraphQL response structure
type Repository struct {
	Name           githubv4.String
	IsFork         githubv4.Boolean // ✅ Correct field (NOT "Fork")
	Owner          struct{ Login githubv4.String }
	StargazerCount githubv4.Int
	ForkCount      githubv4.Int
	Languages      struct {
		Edges []struct {
			Size githubv4.Int
			Node struct {
				Name  githubv4.String
				Color githubv4.String
			}
		}
	} `graphql:"languages(first: 20)"`
}

type Query struct {
	User struct {
		Repositories struct {
			PageInfo struct {
				EndCursor   githubv4.String
				HasNextPage githubv4.Boolean
			}
			Nodes []Repository
		} `graphql:"repositories(first: 100, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC})"`
	} `graphql:"user(login: $login)"`
}

func main() {
	// Load .env in development (optional)
	_ = godotenv.Load()

	cfg, err := loadConfig()
	if err != nil {
		log.Fatalf("❌ Config error: %v", err)
	}

	client := createClient(cfg.AccessToken)
	langStats, overview, err := fetchAllStats(context.Background(), client, cfg)
	if err != nil {
		log.Fatalf("❌ Failed to fetch stats: %v", err)
	}

	// Filter excluded languages
	filtered := make(map[string]int)
	for lang, size := range langStats {
		if !cfg.ExcludedLangs[lang] {
			filtered[lang] = size
		}
	}

	// Sort and limit
	type kv struct{ K string; V int }
	var sorted []kv
	for k, v := range filtered {
		sorted = append(sorted, kv{k, v})
	}
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].V > sorted[j].V })
	if len(sorted) > cfg.LangsLimit {
		sorted = sorted[:cfg.LangsLimit]
	}

	// Compute percentages
	total := 0
	for _, kv := range sorted {
		total += kv.V
	}

	// Build language list with colors
	colors := []string{"#f1e05a", "#3178c6", "#3e4053", "#e34c26", "#563d7c", "#2b7489", "#427819", "#b07219", "#d62929", "#999999"}
	languageList := []LanguageStat{}
	for i, kv := range sorted {
		pct := 0.0
		if total > 0 {
			pct = float64(kv.V) / float64(total)
		}
		color := colors[i%len(colors)]
		if c, ok := knownLanguageColors[kv.K]; ok {
			color = c
		}
		languageList = append(languageList, LanguageStat{
			Name:       kv.K,
			Color:      color,
			Size:       kv.V,
			Percentage: pct,
		})
	}

	// Render both SVGs
	if err := renderLanguagesSVG(TemplateData{cfg.GitHubActor, languageList}); err != nil {
		log.Fatalf("❌ Failed to render languages.svg: %v", err)
	}
	if err := renderOverviewSVG(overview); err != nil {
		log.Fatalf("❌ Failed to render overview.svg: %v", err)
	}

	log.Println("✅ Successfully generated stats/languages.svg and stats/overview.svg")
}

func loadConfig() (*Config, error) {
	actor := os.Getenv("GITHUB_ACTOR")
	token := os.Getenv("ACCESS_TOKEN")
	if actor == "" || token == "" {
		return nil, fmt.Errorf("GITHUB_ACTOR and ACCESS_TOKEN must be set")
	}

	parseList := func(s string) map[string]bool {
		m := make(map[string]bool)
		for _, item := range strings.Split(s, ",") {
			if trimmed := strings.TrimSpace(item); trimmed != "" {
				m[trimmed] = true
			}
		}
		return m
	}

	return &Config{
		GitHubActor:   actor,
		AccessToken:   token,
		ExcludedRepos: parseList(os.Getenv("EXCLUDED_REPO")),
		ExcludedLangs: parseList(os.Getenv("EXCLUDED_LANGS")),
		ExcludeForked: getBoolEnv("EXCLUDE_FORKED", true),
		LangsLimit:    getIntEnv("LANGS_LIMIT", 10),
	}, nil
}

func getBoolEnv(key string, def bool) bool {
	if v := os.Getenv(key); v != "" {
		if b, err := strconv.ParseBool(v); err == nil {
			return b
		}
	}
	return def
}

func getIntEnv(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return n
		}
	}
	return def
}

func createClient(token string) *githubv4.Client {
	src := oauth2.StaticTokenSource(&oauth2.Token{AccessToken: token})
	httpClient := oauth2.NewClient(context.Background(), src)
	return githubv4.NewClient(httpClient)
}

func fetchAllStats(ctx context.Context, client *githubv4.Client, cfg *Config) (map[string]int, OverviewStats, error) {
	stats := make(map[string]int)
	overview := OverviewStats{
		Name:          cfg.GitHubActor,
		Contributions: "—",
		LinesChanged:  "—",
		Views:         "—",
	}

	var cursor *githubv4.String
	login := githubv4.String(cfg.GitHubActor)

	for {
		var query Query
		err := client.Query(ctx, &query, map[string]interface{}{
			"login":  login,
			"cursor": cursor,
		})
		if err != nil {
			return nil, overview, err
		}

		for _, repo := range query.User.Repositories.Nodes {
			repoName := string(repo.Name)
			owner := string(repo.Owner.Login)

			if owner != cfg.GitHubActor {
				continue
			}
			if cfg.ExcludedRepos[repoName] {
				log.Printf("⏭️ Skipping excluded repo: %s", repoName)
				continue
			}
			// ✅ Use IsFork only
			if cfg.ExcludeForked && bool(repo.IsFork) {
				log.Printf("🔀 Skipping forked repo: %s", repoName)
				continue
			}

			overview.Stars += int(repo.StargazerCount)
			overview.Forks += int(repo.ForkCount)
			overview.Repos++

			for _, edge := range repo.Languages.Edges {
				lang := string(edge.Node.Name)
				stats[lang] += int(edge.Size)
			}

			log.Printf("✅ Processed: %s (⭐ %d, 🍴 %d)", repoName, repo.StargazerCount, repo.ForkCount)
		}

		pageInfo := query.User.Repositories.PageInfo
		if !pageInfo.HasNextPage {
			break
		}
		cursor = &pageInfo.EndCursor
		time.Sleep(100 * time.Millisecond)
	}

	return stats, overview, nil
}

func renderLanguagesSVG(data TemplateData) error {
	return renderTemplate("languages.svg.tmpl", "stats/languages.svg", data)
}

func renderOverviewSVG(data OverviewStats) error {
	return renderTemplate("overview.svg.tmpl", "stats/overview.svg", data)
}

func renderTemplate(templateFile, outputFile string, data interface{}) error {
	if err := os.MkdirAll("stats", 0755); err != nil {
		return err
	}
	tmpl := template.Must(template.New(templateFile).Funcs(template.FuncMap{
		"mul": func(a, b float64) float64 { return a * b },
		"add": func(a, b float64) float64 { return a + b },
		"printf": fmt.Sprintf,
	}).ParseFiles("templates/" + templateFile))

	file, err := os.Create(outputFile)
	if err != nil {
		return err
	}
	defer file.Close()
	return tmpl.Execute(file, data)
}

var knownLanguageColors = map[string]string{
	"JavaScript":   "#f1e05a", "TypeScript": "#3178c6", "Python": "#3e4053", "Java": "#b07219",
	"Go":           "#00add8", "Rust": "#dea584", "C++": "#f34b7d", "C": "#555555", "C#": "#178600",
	"PHP":          "#4F5D95", "Ruby": "#701516", "Swift": "#ffac45", "Kotlin": "#A97BFF",
	"Shell":        "#89e051", "HTML": "#e34c26", "CSS": "#563d7c", "SCSS": "#c6538c",
	"Vue":          "#2c3e50", "R": "#198ce7", "Scala": "#dc322f", "Haskell": "#5e5086",
	"Elixir":       "#6e4a7e", "Lua": "#000080", "Perl": "#0298c3", "Objective-C": "#438eff",
	"Assembly":     "#6E4C13", "PowerShell": "#012456", "Dart": "#0175C2", "Groovy": "#e69f56",
}