package danapp

import (
	"context"
	"errors"
	"time"
)

type MailboxClient interface {
	CreateMailbox(ctx context.Context, domainOptions []string) (Mailbox, error)
	FetchOTP(ctx context.Context, mailbox Mailbox, timeout time.Duration) (string, error)
}

type Mailbox struct {
	Address string
	Domain  string
	ID      string
}

var ErrMailboxNotImplemented = errors.New("mailbox api not implemented")

type NullMailboxClient struct{}

func (m NullMailboxClient) CreateMailbox(ctx context.Context, domainOptions []string) (Mailbox, error) {
	return Mailbox{}, ErrMailboxNotImplemented
}

func (m NullMailboxClient) FetchOTP(ctx context.Context, mailbox Mailbox, timeout time.Duration) (string, error) {
	return "", ErrMailboxNotImplemented
}

