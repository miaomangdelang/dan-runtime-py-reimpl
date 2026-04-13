package main

import (
	"context"
	"flag"
	"fmt"
	"os"

	"dan-reimpl/internal/danapp"
)

func main() {
	dir := flag.String("dir", "", "token json directory")
	proxy := flag.String("proxy", "", "explicit proxy address")
	noProxy := flag.Bool("no-proxy", false, "disable all proxies")
	useEnvProxy := flag.Bool("use-env-proxy", false, "allow HTTPS_PROXY / ALL_PROXY")
	flag.Parse()

	cfg, err := danapp.LoadConfig("config.json")
	if err != nil {
		fmt.Fprintln(os.Stderr, "load config:", err)
		os.Exit(1)
	}
	if *dir == "" {
		*dir = cfg.TokenJSONDir
	}
	_ = proxy
	_ = noProxy
	_ = useEnvProxy

	if err := danapp.RefreshTokenJSONDirectory(context.Background(), *dir, nil); err != nil {
		fmt.Fprintln(os.Stderr, "refresh error:", err)
		os.Exit(1)
	}
}

