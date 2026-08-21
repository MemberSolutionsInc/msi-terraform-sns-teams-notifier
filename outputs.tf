output "sns_topic_arns" {
  description = "Map of severity -> SNS topic ARN. Point the sibling msi-terraform-cloudwatch-alarms and msi-terraform-cloudwatch-composite-alarms modules' alarm_actions/ok_actions variables at these."
  value       = { for severity, topic in aws_sns_topic.alerts : severity => topic.arn }
}

output "lambda_function_arn" {
  description = "ARN of the Teams notifier Lambda function."
  value       = aws_lambda_function.notifier.arn
}

output "lambda_function_name" {
  description = "Name of the Teams notifier Lambda function."
  value       = aws_lambda_function.notifier.function_name
}
