package sentinel

import (
	"context"
	"errors"
)

var ErrNotImplemented = errors.New("sentinel solver not implemented")

type Solver interface {
	Solve(ctx context.Context, challenge any) (string, error)
}

