# AWS deployment artifacts (not applied automatically)

These files are templates only. They do not create resources and have not been applied to an AWS account.

## Architecture choices

- EC2 Graviton runs the multi-architecture Docker image.
- Application logs go to stdout/stderr. The Docker `awslogs` driver can send them to a pre-created CloudWatch Logs group.
- S3 is optional for private evidence images; DynamoDB is optional for records/traces. SQLite/local files remain the default for development.
- Use an EC2 instance profile, not static access keys in `.env` or the image. The sample policy is intentionally scoped to a named bucket prefix, one table and one log group. Replace all `REPLACE_WITH_*` values before an administrator reviews and attaches it.
- Keep S3 Block Public Access enabled and use bucket default encryption. The adapter also requests SSE-S3 on uploads. No public-read ACL or public bucket policy is needed.

## IAM setup (manual, review before applying)

1. Create a CloudWatch log group named `/roadguard/app` in the chosen region.
2. Create an EC2 role whose trust relationship permits `ec2.amazonaws.com`, then an instance profile for that role.
3. Replace placeholders in `roadguard-app-policy.json`, review it, and attach it to the role. This is a runtime policy, not a deployment policy.
4. Attach the instance profile to the instance. Boto3 automatically uses the role credentials; do not pass access keys to the app.

The app policy has no EC2 control-plane permissions, no S3 bucket listing/deletion, no DynamoDB table creation/deletion, and no CloudWatch log-group creation. The Docker `awslogs` driver uses the EC2 host's credentials; if using a CloudWatch agent instead, review the separate agent permissions and config.

## Optional DynamoDB table

`dynamodb-table.yaml` creates a pay-per-request table with point-in-time recovery and two indexes used by the adapter. It is a template only. Before deployment, review cost, retention, recovery, and regional requirements. The `all-priority-index` has a single partition key and is suitable only for a small demo; redesign sharding/indexing for production traffic.

Only after approval, a human can review and run something like:

```bash
aws cloudformation deploy \
  --template-file infra/aws/dynamodb-table.yaml \
  --stack-name roadguard-records \
  --region YOUR_REGION
```

Cleanup is also consequential and is intentionally not automated. Inspect the stack and retained data first; deleting the stack/table can permanently delete records. Do not use `--no-fail-on-empty-changeset` to hide unexpected changes.

## Docker logging

After the log group exists and the EC2 role has the sample logging permissions, run the container with the `awslogs` driver (see the root README). The group must exist before starting the container because the policy does not grant `logs:CreateLogGroup`.

## Marketplace COOL compatibility note

Current Marketplace listings are version/CPU-specific. The Graviton4 listing describes an AMI-based environment at `/opt/cool` and preconfigured Python 3.10, 3.11, and 3.12 environments; it recommends c8g/m8g/r8g families and describes m8g.4xlarge or larger for optimal performance. The Graviton5 listing describes Python 3.12–3.14 bindings but is specific to Graviton5. These are not proof that COOL can be injected into this generic container or that every operation/version is API-compatible. Validate listing entitlement, region, supported instance family, AMI/OS, Python ABI, OpenCV version/API, and licensing before choosing an integration. The current benchmark intentionally uses installed `cv2`; it does not claim to run COOL. No Marketplace subscription has been made.
