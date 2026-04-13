package danapp

import (
	"bytes"
	"context"
	"errors"
	"net/http"
	"time"
)

type HTTPSession struct {
	Client *http.Client
}

func NewHTTPSession() *HTTPSession {
	return &HTTPSession{
		Client: &http.Client{Timeout: 30 * time.Second},
	}
}

type RequestOptions struct {
	Method  string
	URL     string
	Headers map[string]string
	Body    []byte
}

var ErrHTTPNotImplemented = errors.New("http request not implemented")

func (s *HTTPSession) Request(ctx context.Context, opt RequestOptions) (*http.Response, []byte, error) {
	// Implement actual HTTP once authorized.
	return nil, nil, ErrHTTPNotImplemented
}

func (s *HTTPSession) JSONRequest(ctx context.Context, opt RequestOptions) (*http.Response, []byte, error) {
	if opt.Headers == nil {
		opt.Headers = map[string]string{}
	}
	opt.Headers["Content-Type"] = "application/json"
	return s.Request(ctx, opt)
}

func (s *HTTPSession) FormRequest(ctx context.Context, opt RequestOptions) (*http.Response, []byte, error) {
	if opt.Headers == nil {
		opt.Headers = map[string]string{}
	}
	opt.Headers["Content-Type"] = "application/x-www-form-urlencoded"
	return s.Request(ctx, opt)
}

func bytesReader(b []byte) *bytes.Reader {
	return bytes.NewReader(b)
}

