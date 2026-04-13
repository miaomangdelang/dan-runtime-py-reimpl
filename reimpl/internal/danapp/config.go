package danapp

import (
	"encoding/json"
	"fmt"
	"os"
)

type Config struct {
	AkFile         string `json:"ak_file"`
	RkFile         string `json:"rk_file"`
	TokenJSONDir   string `json:"token_json_dir"`
	ServerConfigURL string `json:"server_config_url"`
	ServerAPIToken string `json:"server_api_token"`
	DomainReportURL string `json:"domain_report_url"`
	UploadAPIURL   string `json:"upload_api_url"`
	UploadAPIToken string `json:"upload_api_token"`

	OAuthIssuer      string `json:"oauth_issuer"`
	OAuthClientID    string `json:"oauth_client_id"`
	OAuthRedirectURI string `json:"oauth_redirect_uri"`
	EnableOAuth      bool   `json:"enable_oauth"`
	OAuthRequired    bool   `json:"oauth_required"`
}

type WebConfig struct {
	TargetMinTokens       int      `json:"target_min_tokens"`
	AutoFillStartGap      int      `json:"auto_fill_start_gap"`
	CheckIntervalMinutes  int      `json:"check_interval_minutes"`
	ManualDefaultThreads  int      `json:"manual_default_threads"`
	ManualRegisterRetries int      `json:"manual_register_retries"`
	WebToken              string   `json:"web_token"`
	ClientAPIToken        string   `json:"client_api_token"`
	ClientNotice          string   `json:"client_notice"`
	MinimumClientVersion  string   `json:"minimum_client_version"`
	EnabledEmailDomains   []string `json:"enabled_email_domains"`
	MailDomainOptions     []string `json:"mail_domain_options"`
	DefaultProxy          string   `json:"default_proxy"`
	UseRegistrationProxy  bool     `json:"use_registration_proxy"`
	CpaBaseURL            string   `json:"cpa_base_url"`
	CpaToken              string   `json:"cpa_token"`
	MailAPIURL            string   `json:"mail_api_url"`
	MailAPIKey            string   `json:"mail_api_key"`
	Port                  int      `json:"port"`
}

func LoadConfig(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var cfg Config
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, err
	}
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	return &cfg, nil
}

func LoadWebConfig(path string) (*WebConfig, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var cfg WebConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, err
	}
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	return &cfg, nil
}

func (c Config) Validate() error {
	if c.TokenJSONDir == "" {
		return fmt.Errorf("token_json_dir is required")
	}
	if c.EnableOAuth && c.OAuthIssuer == "" {
		return fmt.Errorf("oauth_issuer is required when enable_oauth is true")
	}
	return nil
}

func (c WebConfig) Validate() error {
	if c.Port <= 0 {
		return fmt.Errorf("port is required")
	}
	return nil
}

