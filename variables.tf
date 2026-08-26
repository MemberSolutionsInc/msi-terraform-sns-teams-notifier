variable "severities" {
  description = "List of alarm severities to provision an SNS topic for. Each severity gets its own topic so CloudWatch alarms can route by severity."
  type        = list(string)
  default     = ["critical", "warning"]
}

variable "team_webhook_map" {
  description = <<-EOT
    Map of owning team name -> Microsoft Teams Incoming Webhook URL. The Lambda notifier
    looks up the alarm's `team` tag against this map to decide which Teams channel to post to.
    Webhook URLs are effectively secrets - supply this via TF_VAR_team_webhook_map or a
    secrets-backed source, never commit real values.
  EOT
  type        = map(string)
  sensitive   = true
}

variable "default_webhook_url" {
  description = "Fallback Microsoft Teams Incoming Webhook URL used when an alarm's `team` tag is missing or not present in team_webhook_map. Treat as a secret - supply via TF_VAR_default_webhook_url or a secrets-backed source."
  type        = string
  sensitive   = true
}

variable "tier2_dashboard_url_template" {
  description = "URL template for the Tier 2 dashboard, with {service} and {env} placeholders substituted by the Lambda notifier."
  type        = string
  default     = "https://dashboards.internal.membersolutions.com/tier2/{service}?env={env}"
}

variable "tier3_dashboard_url_template" {
  description = "URL template for the Tier 3 dashboard, with {service} and {env} placeholders substituted by the Lambda notifier."
  type        = string
  default     = "https://dashboards.internal.membersolutions.com/tier3/{service}?env={env}"
}

variable "lambda_function_name" {
  description = "Name of the Lambda notifier function."
  type        = string
  default     = "sns-teams-notifier"
}

variable "tags" {
  description = "Tags applied to the SNS topics, Lambda function, and its IAM role."
  type        = map(string)
  default     = {}
}
