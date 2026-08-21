locals {
  lambda_source_dir = "${path.module}/files"
}

# ---------------------------------------------------------------------------
# SNS topics - one per severity
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  for_each = toset(var.severities)

  name = "alerts-${each.value}"
}

# ---------------------------------------------------------------------------
# Lambda notifier
# ---------------------------------------------------------------------------

data "archive_file" "notifier" {
  type        = "zip"
  source_file = "${local.lambda_source_dir}/notifier.py"
  output_path = "${path.module}/.build/notifier.zip"
}

resource "aws_iam_role" "lambda" {
  name = "${var.lambda_function_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "lambda" {
  name = "${var.lambda_function_name}-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchAlarmMetadata"
        Effect = "Allow"
        Action = [
          "cloudwatch:ListTagsForResource",
          "cloudwatch:DescribeAlarms",
        ]
        Resource = "*"
      },
      {
        Sid    = "LambdaLogging"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

resource "aws_lambda_function" "notifier" {
  function_name = var.lambda_function_name
  role          = aws_iam_role.lambda.arn
  handler       = "notifier.handler"
  runtime       = "python3.12"
  timeout       = 15
  memory_size   = 128

  filename         = data.archive_file.notifier.output_path
  source_code_hash = data.archive_file.notifier.output_base64sha256

  environment {
    variables = {
      TEAM_WEBHOOK_MAP             = jsonencode(var.team_webhook_map)
      DEFAULT_WEBHOOK_URL          = var.default_webhook_url
      TIER2_DASHBOARD_URL_TEMPLATE = var.tier2_dashboard_url_template
      TIER3_DASHBOARD_URL_TEMPLATE = var.tier3_dashboard_url_template
    }
  }
}

# ---------------------------------------------------------------------------
# SNS -> Lambda wiring, one subscription + permission per severity
# ---------------------------------------------------------------------------

resource "aws_sns_topic_subscription" "notifier" {
  for_each = aws_sns_topic.alerts

  topic_arn = each.value.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.notifier.arn
}

resource "aws_lambda_permission" "sns_invoke" {
  for_each = aws_sns_topic.alerts

  statement_id  = "AllowSNS-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.notifier.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = each.value.arn
}
