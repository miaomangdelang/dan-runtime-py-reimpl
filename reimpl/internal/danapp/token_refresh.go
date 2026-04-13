package danapp

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
)

var ErrTokenRefreshNotImplemented = errors.New("token refresh not implemented")

type TokenRefresher interface {
	Refresh(ctx context.Context, tok *OAuthTokens) (*OAuthTokens, error)
}

func RefreshTokenJSONDirectory(ctx context.Context, dir string, r TokenRefresher) error {
	if r == nil {
		return ErrTokenRefreshNotImplemented
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return err
	}
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		if filepath.Ext(e.Name()) != ".json" {
			continue
		}
		path := filepath.Join(dir, e.Name())
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		var tok OAuthTokens
		if err := json.Unmarshal(data, &tok); err != nil {
			return err
		}
		newTok, err := r.Refresh(ctx, &tok)
		if err != nil {
			return err
		}
		out, err := json.MarshalIndent(newTok, "", "  ")
		if err != nil {
			return err
		}
		if err := os.WriteFile(path, out, 0o600); err != nil {
			return err
		}
	}
	return nil
}

