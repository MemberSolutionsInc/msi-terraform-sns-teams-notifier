"""
SNS -> Microsoft Teams notifier for CloudWatch Alarms.

Triggered by an SNS subscription. Each SNS record's Message body is the
CloudWatch Alarm JSON payload (https://docs.aws.amazon.com/AmazonCloudWatch/
latest/monitoring/US_SetupSNS.html). This handler enriches that payload with
the alarm's tags (service/env/severity/team/runbook), builds a Microsoft
Teams Adaptive Card, and POSTs it to the Teams Incoming Webhook associated
with the alarm's owning team.

Deliberately dependency-free (stdlib only: json, os, urllib.request, plus
boto3 which is provided by the Lambda runtime) so no extra layer/zip
dependency is required.
"""
import json
import os
import time
import urllib.request
import urllib.error

import boto3

cloudwatch = boto3.client("cloudwatch")
secretsmanager = boto3.client("secretsmanager")

TEAMS_CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"
TEAMS_CARD_VERSION = "1.4"

# In-memory cache for secrets fetched via DEFAULT_WEBHOOK_SECRET_ARN, so a
# burst of alarms firing together doesn't hit Secrets Manager once per
# invocation. Lives for the container's lifetime; a cold start always
# refetches.
_SECRET_CACHE_TTL_SECONDS = 300
_secret_cache = {}


def _get_env_json(name, default=None):
    raw = os.environ.get(name)
    if not raw:
        return default if default is not None else {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default if default is not None else {}


def get_alarm_tags(alarm_arn):
    """Look up an alarm's tags via CloudWatch, returning a plain dict.

    Returns an empty dict (rather than raising) if the lookup fails, so a
    tagging problem degrades to a less-informative notification instead of
    a dropped one.
    """
    if not alarm_arn:
        return {}
    try:
        response = cloudwatch.list_tags_for_resource(ResourceARN=alarm_arn)
        return {tag["Key"]: tag["Value"] for tag in response.get("Tags", [])}
    except Exception as exc:  # noqa: BLE001 - best-effort enrichment
        print(f"WARNING: failed to fetch tags for {alarm_arn}: {exc}")
        return {}


def render_url(template, service, env):
    if not template:
        return None
    return template.format(service=service or "unknown", env=env or "unknown")


def get_cached_secret(secret_arn):
    """Fetch a Secrets Manager secret's string value, cached in-memory.

    Returns None (rather than raising) on failure, so a Secrets Manager
    problem degrades to a dropped notification instead of a crashed
    invocation - consistent with get_alarm_tags' error handling above.
    """
    now = time.monotonic()
    cached = _secret_cache.get(secret_arn)
    if cached and (now - cached["fetched_at"]) < _SECRET_CACHE_TTL_SECONDS:
        return cached["value"]

    try:
        response = secretsmanager.get_secret_value(SecretId=secret_arn)
        value = response["SecretString"]
    except Exception as exc:  # noqa: BLE001 - resolve_webhook_url logs the eventual None
        print(f"WARNING: failed to fetch secret {secret_arn}: {exc}")
        value = None

    _secret_cache[secret_arn] = {"value": value, "fetched_at": now}
    return value


def resolve_webhook_url(team):
    team_webhook_map = _get_env_json("TEAM_WEBHOOK_MAP", {})
    if team and team in team_webhook_map:
        return team_webhook_map[team]

    default_webhook_secret_arn = os.environ.get("DEFAULT_WEBHOOK_SECRET_ARN")
    if default_webhook_secret_arn:
        return get_cached_secret(default_webhook_secret_arn)

    # Deprecated path: URL baked directly into the environment.
    return os.environ.get("DEFAULT_WEBHOOK_URL")


def severity_color(severity):
    mapping = {
        "critical": "Attention",
        "warning": "Warning",
    }
    return mapping.get((severity or "").lower(), "Default")


def build_adaptive_card(alarm, tags):
    alarm_name = alarm.get("AlarmName", "Unknown alarm")
    alarm_description = alarm.get("AlarmDescription") or "(no description)"
    new_state = alarm.get("NewStateValue", "UNKNOWN")
    new_state_reason = alarm.get("NewStateReason", "")

    service = tags.get("service")
    env = tags.get("env")
    severity = tags.get("severity")
    team = tags.get("team")
    runbook = tags.get("runbook")

    tier2_url = render_url(os.environ.get("TIER2_DASHBOARD_URL_TEMPLATE"), service, env)
    tier3_url = render_url(os.environ.get("TIER3_DASHBOARD_URL_TEMPLATE"), service, env)

    facts = [
        {"title": "Service", "value": service or "unknown"},
        {"title": "Environment", "value": env or "unknown"},
        {"title": "Severity", "value": severity or "unknown"},
        {"title": "Owning team", "value": team or "unassigned"},
        {"title": "New state", "value": new_state},
        {"title": "Reason", "value": new_state_reason or "(none provided)"},
    ]

    actions = []
    if runbook:
        actions.append({
            "type": "Action.OpenUrl",
            "title": "Runbook",
            "url": runbook,
        })
    if tier2_url:
        actions.append({
            "type": "Action.OpenUrl",
            "title": "Tier 2 dashboard",
            "url": tier2_url,
        })
    if tier3_url:
        actions.append({
            "type": "Action.OpenUrl",
            "title": "Tier 3 dashboard",
            "url": tier3_url,
        })

    card_body = [
        {
            "type": "TextBlock",
            "text": f"CloudWatch Alarm: {alarm_name}",
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
            "color": severity_color(severity),
        },
        {
            "type": "TextBlock",
            "text": alarm_description,
            "wrap": True,
            "isSubtle": True,
        },
        {
            "type": "FactSet",
            "facts": facts,
        },
    ]

    if alarm.get("Trigger"):
        trigger = alarm["Trigger"]
        metric_name = trigger.get("MetricName", "unknown metric")
        namespace = trigger.get("Namespace", "")
        card_body.append({
            "type": "TextBlock",
            "text": f"Metric: {namespace}/{metric_name}",
            "wrap": True,
            "isSubtle": True,
            "spacing": "Small",
        })

    card = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": TEAMS_CARD_SCHEMA,
                    "type": "AdaptiveCard",
                    "version": TEAMS_CARD_VERSION,
                    "body": card_body,
                    "actions": actions,
                },
            }
        ],
    }
    return card


def post_to_teams(webhook_url, card):
    if not webhook_url:
        print("ERROR: no Teams webhook URL resolved for this alarm; dropping notification")
        return False

    data = json.dumps(card).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.getcode()
            print(f"Teams webhook responded with status {status}")
            return 200 <= status < 300
    except urllib.error.HTTPError as exc:
        print(f"ERROR: Teams webhook returned HTTP {exc.code}: {exc.read()}")
        return False
    except urllib.error.URLError as exc:
        print(f"ERROR: failed to reach Teams webhook: {exc.reason}")
        return False


def handler(event, context):
    results = []
    for record in event.get("Records", []):
        sns = record.get("Sns", {})
        message_raw = sns.get("Message", "{}")
        try:
            alarm = json.loads(message_raw)
        except ValueError:
            print(f"WARNING: could not parse SNS message as JSON: {message_raw!r}")
            continue

        alarm_arn = alarm.get("AlarmArn")
        tags = get_alarm_tags(alarm_arn)

        card = build_adaptive_card(alarm, tags)
        webhook_url = resolve_webhook_url(tags.get("team"))
        sent = post_to_teams(webhook_url, card)
        results.append({"alarm": alarm.get("AlarmName"), "sent": sent})

    return {"results": results}
