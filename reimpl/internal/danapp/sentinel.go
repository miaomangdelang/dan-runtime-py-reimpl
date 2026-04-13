package danapp

import (
	"context"
	"errors"
)

type SentinelSolver interface {
	Solve(ctx context.Context, challenge any) (string, error)
}

var ErrSentinelNotImplemented = errors.New("sentinel solver not implemented")

type NullSentinel struct{}

func (s NullSentinel) Solve(ctx context.Context, challenge any) (string, error) {
	return "", ErrSentinelNotImplemented
}

