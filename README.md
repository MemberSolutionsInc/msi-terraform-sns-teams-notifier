# msi-terraform-sns-teams-notifier

Alert delivery module for MemberSolutions' CloudWatch observability stack.
Provisions per-severity SNS topics fanned out to a Lambda notifier that
posts a Microsoft Teams Adaptive Card, enforcing the org's alert context
requirements.

This module is one of a set of independently-versioned modules split out of
a single CloudWatch observability initiative, so that bumping one module's
version (e.g. the alarms module) doesn't force a version bump on the others
(this notifier, composite alarms, etc).

## Delivery path

```
CloudWatch Alarm -> SNS Topic (per severity) -> Lambda notifier -> Teams Incoming Webhook
```

1. A CloudWatch Alarm (defined by the sibling `msi-terraform-cloudwatch-alarms`
   module, or `msi-terraform-cloudwatch-composite-alarms`) changes state and
   publishes to one of the SNS topics this module creates, chosen by the
   alarm's severity.
2. SNS invokes the `notifier` Lambda function.
3. The Lambda parses the CloudWatch Alarm JSON payload from the SNS message,
   then calls `cloudwatch:ListTagsForResource` against the alarm's ARN to
   look up the `service`, `env`, `severity`, `team`, and `runbook` tags set
   on the alarm.
4. The Lambda builds a Microsoft Teams Adaptive Card containing the alarm
   name, service, environment, severity, owning team, new state, reason,
   a runbook link, and links to the Tier 2/Tier 3 dashboards for that
   service/environment.
5. The Lambda looks up which Teams Incoming Webhook to POST the card to
   based on the alarm's `team` tag (via `team_webhook_map`), falling back
   to `default_webhook_url` if the team isn't found in the map.

Routing to the correct Teams channel is entirely driven by the `team` /
`severity` tags set on each CloudWatch alarm by the alarms module - this
module does not need to know about individual alarms or services ahead of
time.

## Why a Lambda notifier instead of AWS Chatbot

Per this org's monitoring standard, every alert delivered to Teams must
carry: service name, environment, severity, owning team, the resource/entity
identifier, uptime impact (if applicable), a link to Tier 2/3 dashboards,
and a runbook link.

AWS Chatbot can post CloudWatch alarms to Microsoft Teams directly, but its
default card formatting is a generic rendering of the SNS message and can't
guarantee all of the fields above without significant custom card templating
work that Chatbot doesn't natively support. A small, purpose-built Lambda
that parses the alarm's tags and description and constructs the Adaptive
Card directly gives full control over the card layout and guarantees the
required fields are present (or explicitly shown as missing), at the cost of
maintaining ~200 lines of Python instead of a managed integration.

## Usage

```hcl
# Secret created and populated out-of-band (e.g. `aws secretsmanager
# create-secret`) - its value never flows through Terraform/CI. This
# resource only adopts it (via an `import` block) for tag/description
# upkeep and to give the module something to grant IAM read access to.
resource "aws_secretsmanager_secret" "teams_webhook" {
  name = "my-account/my-service/teams-webhook-url"
}

module "teams_notifier" {
  source = "git::https://github.com/MemberSolutionsInc/msi-terraform-sns-teams-notifier.git?ref=v0.3.0"

  severities            = ["critical", "warning"]
  lambda_function_name  = "sns-teams-notifier"

  # Preferred: the Lambda fetches this live at invoke time (cached 5min),
  # so rotating the secret's value needs no Terraform apply or redeploy.
  default_webhook_secret_arn = aws_secretsmanager_secret.teams_webhook.arn

  # team_webhook_map still requires literal values today - sensitive,
  # supply via TF_VAR_team_webhook_map or a secrets-backed source. Never
  # commit real values.
  team_webhook_map = var.team_webhook_map

  tier2_dashboard_url_template = "https://dashboards.internal.membersolutions.com/tier2/{service}?env={env}"
  tier3_dashboard_url_template = "https://dashboards.internal.membersolutions.com/tier3/{service}?env={env}"

  tags = local.tags
}

# Wire the sibling alarms module at the SNS topics this module creates.
# This module provisions one topic per severity (ALARM and OK notifications
# share the same topic — the Lambda differentiates by the payload's
# NewStateValue), but msi-terraform-cloudwatch-alarms' sns_topic_arns input
# expects 4 distinct keys (separate alarm/ok arns per severity). Map the
# same topic ARN into both keys per severity rather than provisioning
# duplicate topics.
module "cloudwatch_alarms" {
  source = "git::https://github.com/MemberSolutionsInc/msi-terraform-cloudwatch-alarms.git?ref=v0.2.0"

  sns_topic_arns = {
    critical_alarm_arn = module.teams_notifier.sns_topic_arns["critical"]
    critical_ok_arn    = module.teams_notifier.sns_topic_arns["critical"]
    warning_alarm_arn  = module.teams_notifier.sns_topic_arns["warning"]
    warning_ok_arn     = module.teams_notifier.sns_topic_arns["warning"]
  }
  # ...
}
```

### Supplying sensitive inputs

`team_webhook_map`, `default_webhook_url`, and `default_webhook_secret_arn`'s
target all contain Microsoft Teams Incoming Webhook URLs, which are
effectively secrets (anyone with the URL can post to the channel).

- **Never** hardcode real values in `.tf`/`.tfvars` files or commit them.
- Preferred: create the secret out-of-band (`aws secretsmanager
  create-secret`) with the real value, adopt the container into Terraform
  via an `import` block for tag upkeep only, and pass its ARN as
  `default_webhook_secret_arn`. The value never flows through Terraform
  state, CI logs, or a GitHub Actions secret, and the Lambda picks up
  rotations without a redeploy.
- Deprecated: `default_webhook_url`, baked into the Lambda's environment at
  apply time. Supply via `TF_VAR_default_webhook_url` or a secrets-backed
  data source if used - rotation requires a new apply.
- `team_webhook_map` still only supports literal values (no secret-ARN
  equivalent yet) - supply via `TF_VAR_team_webhook_map` or a secrets-backed
  source.

## Inputs

| Name | Description | Type | Default | Required | Sensitive |
|------|-------------|------|---------|----------|-----------|
| `severities` | List of alarm severities to provision an SNS topic for | `list(string)` | `["critical", "warning"]` | no | no |
| `team_webhook_map` | Map of owning team name -> Teams Incoming Webhook URL | `map(string)` | n/a | yes | yes |
| `default_webhook_secret_arn` | ARN of a Secrets Manager secret holding the fallback webhook URL, fetched live at invoke time (cached 5min). Preferred; takes precedence over `default_webhook_url` | `string` | `""` | no | no |
| `default_webhook_url` | Deprecated: fallback Teams Incoming Webhook URL baked into the Lambda's environment at apply time | `string` | `""` | no | yes |
| `tier2_dashboard_url_template` | URL template for the Tier 2 dashboard (`{service}`/`{env}` placeholders) | `string` | `"https://dashboards.internal.membersolutions.com/tier2/{service}?env={env}"` | no | no |
| `tier3_dashboard_url_template` | URL template for the Tier 3 dashboard (`{service}`/`{env}` placeholders) | `string` | `"https://dashboards.internal.membersolutions.com/tier3/{service}?env={env}"` | no | no |
| `lambda_function_name` | Name of the Lambda notifier function | `string` | `"sns-teams-notifier"` | no | no |
| `tags` | Tags applied to the SNS topics, Lambda function, and its IAM role | `map(string)` | `{}` | no | no |

## Outputs

| Name | Description |
|------|-------------|
| `sns_topic_arns` | Map of severity -> SNS topic ARN. Point the sibling `msi-terraform-cloudwatch-alarms` and `msi-terraform-cloudwatch-composite-alarms` modules' `alarm_actions`/`ok_actions` variables at these. |
| `lambda_function_arn` | ARN of the Teams notifier Lambda function. |
| `lambda_function_name` | Name of the Teams notifier Lambda function. |

## Requirements

| Name | Version |
|------|---------|
| terraform | ~> 1.0 |
| aws | ~> 5.0 |
| archive | ~> 2.0 |

## Notifier Lambda

The Lambda handler lives at `files/notifier.py`, runs on `python3.12`, and
has no third-party dependencies (stdlib `json`/`os`/`urllib.request` plus
`boto3`, which is provided by the Lambda runtime) - this keeps the deployment
package dependency-free with no layer required.
