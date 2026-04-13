package danapp

import (
	"context"
	"errors"
)

type OAuthTokens struct {
	AccessToken  string `json:"access_token"`
	RefreshToken string `json:"refresh_token"`
	SessionToken string `json:"session_token"`
	ExpiresAt    int64  `json:"expires_at"`
}

var ErrOAuthNotImplemented = errors.New("oauth flow not implemented")

func (a *App) PerformOAuthFlow(ctx context.Context, email, password string) (*OAuthTokens, error) {
	// Placeholder for OAuth and token capture.
	return nil, ErrOAuthNotImplemented
}

