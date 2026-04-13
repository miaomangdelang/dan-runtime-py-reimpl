package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"strings"

	"dan-reimpl/internal/danapp"
)

type stringSlice []string

func (s *stringSlice) String() string { return strings.Join(*s, ",") }
func (s *stringSlice) Set(v string) error {
	if v == "" {
		return nil
	}
	*s = append(*s, v)
	return nil
}

func main() {
	var (
		count           = flag.Int("count", 1, "number of accounts to register")
		countShort      = flag.Int("n", 1, "number of accounts to register (shorthand)")
		output          = flag.String("output", "registered_accounts.txt", "output file")
		proxy           = flag.String("proxy", "", "explicit proxy address")
		noProxy         = flag.Bool("no-proxy", false, "disable all proxies")
		useEnvProxy     = flag.Bool("use-env-proxy", false, "allow HTTPS_PROXY / ALL_PROXY")
		noUpload        = flag.Bool("no-upload", false, "skip CPA upload")
		noOAuth         = flag.Bool("no-oauth", false, "skip OAuth flow")
		oauthNotReq     = flag.Bool("oauth-not-required", false, "do not abort if OAuth fails")
	)
	var domains stringSlice
	flag.Var(&domains, "domains", "mail domains, e.g. --domains a.com --domains b.com")
	flag.Parse()

	cfg, err := danapp.LoadConfig("config.json")
	if err != nil {
		fmt.Fprintln(os.Stderr, "load config:", err)
		os.Exit(1)
	}

	app := danapp.NewApp(cfg)
	app.OutputPath = *output
	app.Proxy = *proxy
	app.DisableProxy = *noProxy
	app.UseEnvProxy = *useEnvProxy
	app.NoUpload = *noUpload
	app.NoOAuth = *noOAuth
	app.OAuthNotRequired = *oauthNotReq

	finalCount := *count
	if *countShort != 1 {
		finalCount = *countShort
	}

	_ = domains // reserved for mailbox selection once implemented

	if err := app.Run(context.Background(), finalCount); err != nil {
		fmt.Fprintln(os.Stderr, "run error:", err)
		os.Exit(1)
	}
}

