package danapp

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"time"
)

var ErrNotImplemented = errors.New("not implemented")

type App struct {
	Config           *Config
	HTTP             *HTTPSession
	Mailbox          MailboxClient
	Sentinel         SentinelSolver
	Logger           *log.Logger
	OutputPath       string
	NoUpload         bool
	NoOAuth          bool
	OAuthNotRequired bool
	Proxy            string
	DisableProxy     bool
	UseEnvProxy      bool
}

type AccountResult struct {
	Email       string
	Password    string
	AccountID   string
	TokenPath   string
	CreatedAt   time.Time
	Notes       string
}

func NewApp(cfg *Config) *App {
	return &App{
		Config:     cfg,
		Logger:     log.New(os.Stdout, "", log.LstdFlags),
		OutputPath: "registered_accounts.txt",
	}
}

func (a *App) Run(ctx context.Context, count int) error {
	if count <= 0 {
		return fmt.Errorf("count must be > 0")
	}
	for i := 0; i < count; i++ {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		res, err := a.RegisterOne(ctx)
		if err != nil {
			return err
		}
		if res != nil {
			if err := a.AppendResult(res); err != nil {
				return err
			}
		}
	}
	return nil
}

func (a *App) RegisterOne(ctx context.Context) (*AccountResult, error) {
	// Placeholder for the full registration flow.
	// Implementations should be authorized and compliant with target services.
	return nil, ErrNotImplemented
}

func (a *App) AppendResult(res *AccountResult) error {
	if a.OutputPath == "" {
		return nil
	}
	line := fmt.Sprintf("%s\t%s\t%s\t%s\n", res.Email, res.Password, res.AccountID, res.TokenPath)
	return appendLine(a.OutputPath, line)
}

func (a *App) SaveTokenJSON(email string, data []byte) (string, error) {
	if a.Config == nil {
		return "", fmt.Errorf("config required")
	}
	if a.Config.TokenJSONDir == "" {
		return "", fmt.Errorf("token_json_dir is empty")
	}
	if err := os.MkdirAll(a.Config.TokenJSONDir, 0o755); err != nil {
		return "", err
	}
	name := fmt.Sprintf("%s.json", sanitizeFileName(email))
	path := filepath.Join(a.Config.TokenJSONDir, name)
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return "", err
	}
	return path, nil
}

func sanitizeFileName(s string) string {
	out := make([]rune, 0, len(s))
	for _, r := range s {
		if r == '@' || r == '.' || r == '-' || r == '_' {
			out = append(out, r)
			continue
		}
		if r >= 'a' && r <= 'z' {
			out = append(out, r)
			continue
		}
		if r >= 'A' && r <= 'Z' {
			out = append(out, r)
			continue
		}
		if r >= '0' && r <= '9' {
			out = append(out, r)
			continue
		}
		out = append(out, '_')
	}
	return string(out)
}

