package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"

	"dan-reimpl/internal/danapp"
)

type statusResponse struct {
	OK      bool   `json:"ok"`
	Message string `json:"message"`
}

func main() {
	log.SetFlags(log.LstdFlags)
	webCfg, err := danapp.LoadWebConfig("config/web_config.json")
	if err != nil {
		log.Println("load web config:", err)
		os.Exit(1)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprintln(w, "<html><body><h1>dan-web (reimpl)</h1><p>Stub UI</p></body></html>")
	})
	mux.HandleFunc("/api/bootstrap", jsonOK("bootstrap ok"))
	mux.HandleFunc("/api/login", jsonOK("login ok"))
	mux.HandleFunc("/api/logout", jsonOK("logout ok"))
	mux.HandleFunc("/api/status", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(statusResponse{OK: true, Message: "stub"})
	})
	mux.HandleFunc("/api/config", jsonOK("config updated (stub)"))
	mux.HandleFunc("/api/manual-register", jsonOK("manual register queued (stub)"))
	mux.HandleFunc("/api/reconcile", jsonOK("reconcile triggered (stub)"))
	mux.HandleFunc("/api/fill", jsonOK("fill triggered (stub)"))

	addr := fmt.Sprintf("0.0.0.0:%d", webCfg.Port)
	log.Printf("[dan-web] listening on http://%s", addr)
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Println("server error:", err)
		os.Exit(1)
	}
}

func jsonOK(msg string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(statusResponse{OK: true, Message: msg})
	}
}

