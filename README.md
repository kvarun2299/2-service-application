# Fieldnote Products

A small two-service product catalog for local Docker Compose and manual deployment to Amazon ECS on Fargate. The web container serves static files; the FastAPI container owns all database access. Both services use the same REST paths in development and behind the ALB.

## Folder structure

```text
2-service-application/
├── api/
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
├── web/
│   ├── Dockerfile
│   ├── app.js
│   ├── index.html
│   ├── nginx.conf
│   ├── nginx.local.conf
│   └── styles.css
├── .env.example
├── .gitignore
├── buildspec.yaml
└── docker-compose.yml
```

- `api/` contains the FastAPI REST API, SQLAlchemy MySQL model, schema initialization, and API image.
- `web/` contains the responsive browser UI and its unprivileged Nginx image. Compose mounts `nginx.local.conf` to proxy `/api/` to the API container; the production image uses `nginx.conf` because the ALB routes `/api/*` directly to the API service.
- `.env.example` is a template for local-only credentials. Never commit `.env` or put production credentials in source code.
- `buildspec.yaml` builds both Linux/x86_64 container images, pushes them to ECR with a source/build-specific tag, and publishes the image URIs as a build artifact.
- `docker-compose.yml` starts the web service, API service, and MySQL for local development.

## Local development

Prerequisites: Docker Desktop with Docker Compose.

1. Copy `.env.example` to `.env` and change the sample local passwords. These are development-only values.
2. From this directory, run:

   ```powershell
   docker compose up --build
   ```

3. Open <http://localhost:8080>. Add, list, and delete products in the UI.
4. Optional checks:

   ```powershell
   curl.exe http://localhost:8080/health
   curl.exe http://localhost:8080/api/products
   ```

   The API health checks are also available at `http://localhost:8080/health` (web) and `http://localhost:8080/api/health/db` (through the local proxy). The API container exposes `/health` on port 8000.

5. Stop the stack with `Ctrl+C`. Remove the local database volume only when you intentionally want to delete local products:

   ```powershell
   docker compose down
   docker compose down --volumes
   ```

The API creates the `products` table when it starts. It retries database initialization with backoff when MySQL is temporarily unavailable, and returns HTTP 503 for database errors during requests. `/health` checks that the API process is available; `/api/health/db` also verifies a database connection.

## Application and database

The database is `appdb`. The API creates this table if it does not already exist:

| Column | Type | Rules |
| --- | --- | --- |
| `id` | `INT` | Primary key, auto-increment |
| `name` | `VARCHAR(255)` | Not null |
| `price` | `DECIMAL(10,2)` | Not null |
| `description` | `TEXT` | Nullable |
| `created_at` | `TIMESTAMP` | Defaults to current timestamp |

The API reads `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD` from its environment. It uses SQLAlchemy with the PyMySQL driver and `utf8mb4`.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/products` | List products |
| `POST` | `/api/products` | Create a product (`name`, `price`, optional `description`) |
| `DELETE` | `/api/products/{id}` | Delete a product |
| `GET` | `/health` | API process health |
| `GET` | `/api/health/db` | API and database health |

## Deploy to AWS ECS Fargate

This guide uses the AWS Console and AWS CLI for image builds/pushes. It does not require Kubernetes, Terraform, or AWS credentials in application files. Choose a region and replace `<REGION>`, `<ACCOUNT_ID>`, and `<VPC_ID>` in commands. Use two Availability Zones for the public and private subnets.

### 1. Create the network

Use an existing VPC or create one with:

- An Internet Gateway and at least two **public subnets** in separate Availability Zones for the ALB.
- At least two **private application subnets** in separate Availability Zones for the ECS tasks.
- At least two **private database subnets** in separate Availability Zones for the RDS DB subnet group.
- A NAT Gateway and private-subnet routes to it for Fargate tasks to pull ECR images and send logs. For a VPC without NAT, configure the required ECR API, ECR Docker, S3, and CloudWatch Logs VPC endpoints instead.

The ECS services and RDS must not be placed in public subnets. Set RDS **Public access** to **No**.

Place the ECS tasks and RDS in the **same VPC**. The VPC's local route provides private-subnet connectivity; no public IP or internet route is needed for API-to-RDS traffic. Keep VPC DNS resolution and DNS hostnames enabled so the RDS endpoint resolves from the API task.

### 2. Create the RDS MySQL database

1. In **RDS → Databases → Create database**, choose **Standard create**, **MySQL**, and a supported MySQL 8 version.
2. Choose a DB instance identifier, instance size, storage, and credentials. Store the username/password securely; do not put real values in source code or container images. For production, enable backups and use Multi-AZ as appropriate.
3. Set the initial database name to `appdb`.
4. Select **Private access** (not publicly accessible) and create a DB subnet group from the private database subnets.
5. Attach the RDS security group described below. Copy the RDS endpoint hostname (not a URL); it is the value of `DB_HOST`.
6. Use a dedicated application database user, not the RDS master user. Grant it `CREATE`, `SELECT`, `INSERT`, and `DELETE` on `appdb.*`; `CREATE` is required because the API creates the `products` table on startup. Store its username/password securely for the ECS task secrets.
7. The API creates the `products` table on startup. No public database access is needed.

### 3. Create ECR repositories and push images

Create two private repositories in **ECR → Repositories → Create repository**:

- `fieldnote-web`
- `fieldnote-api`

Authenticate Docker and build/push from this project directory. Replace the placeholders:

```powershell
aws ecr get-login-password --region <REGION> | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com

docker build -t fieldnote-web:latest .\web
docker tag fieldnote-web:latest <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/fieldnote-web:latest
docker push <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/fieldnote-web:latest

docker build -t fieldnote-api:latest .\api
docker tag fieldnote-api:latest <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/fieldnote-api:latest
docker push <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/fieldnote-api:latest
```

#### Build and push with AWS CodeBuild

The root `buildspec.yaml` performs the same two image builds and pushes both images to the `fieldnote-web` and `fieldnote-api` ECR repositories. Create those repositories first, then create a CodeBuild project with:

- This project as its source and the project root as its build location.
- A current AWS-managed Linux standard image, Docker support, and **Privileged** mode enabled (required for Docker image builds).
- The buildspec file name set to `buildspec.yaml` in the build project's Buildspec override. CodeBuild otherwise looks for `buildspec.yml` by default.
- A service role that can write build logs and push to both ECR repositories (`ecr:GetAuthorizationToken` plus the required ECR layer upload and `ecr:PutImage` permissions).
- The same AWS region as the ECR repositories. The buildspec uses CodeBuild's `AWS_DEFAULT_REGION` and resolves the account ID from the CodeBuild role; it contains no AWS credentials.

The optional CodeBuild environment variables `WEB_REPOSITORY` and `API_REPOSITORY` default to `fieldnote-web` and `fieldnote-api`. Each build tags both images with the first 12 characters of the source revision (or the CodeBuild build ID if no source revision is available), pushes them, and publishes `image-detail.json` plus `image-tag.txt`. Use the image URIs from `image-detail.json` when registering updated ECS task-definition revisions; pushing images alone does not update running ECS services.

### 4. Create security groups

Create these security groups in the application VPC. Use security-group references rather than broad CIDR ranges for service-to-service rules.

| Security group | Inbound rules | Outbound |
| --- | --- | --- |
| ALB | TCP 80 from `0.0.0.0/0` (and `::/0` only if IPv6 is configured) | Default outbound, or TCP 8080 to Web SG and TCP 8000 to API SG |
| Web ECS | TCP 8080 **from ALB SG only** | Default outbound |
| API ECS | TCP 8000 **from ALB SG only** | TCP 3306 to RDS SG; TCP 443 to NAT-routed AWS services or required endpoint SGs |
| RDS | TCP 3306 **from API ECS SG only** | Default outbound |

Do not add an internet or public-CIDR rule for port 3306. Do not permit direct internet ingress to either ECS task security group. The API ingress source is the ALB security group because the ALB forwards `/api/*` requests directly to API tasks.

For the API-to-RDS connection to work, attach these exact groups to the resources: API ECS tasks use the API ECS SG, and RDS uses the RDS SG. The API SG needs outbound TCP 3306 **to the RDS SG**; the RDS SG needs inbound TCP 3306 **from the API ECS SG**. Security groups are stateful, so return traffic is automatically allowed. Do not use the ALB or Web SG as the RDS rule's source. If API SG egress is restricted, separately allow outbound HTTPS needed for image pulls and logs through NAT or VPC endpoints; that egress is independent of the private database connection.

### 5. Create ECS task definitions

Create an ECS cluster with the **AWS Fargate** infrastructure. Register two task definitions with the Fargate launch type, Linux/X86_64 (matching the images), and `awsvpc` network mode:

| Task definition | Image | Container port | Logs |
| --- | --- | --- | --- |
| `fieldnote-web` | `<ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/fieldnote-web:latest` | TCP 8080 | CloudWatch Logs |
| `fieldnote-api` | `<ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/fieldnote-api:latest` | TCP 8000 | CloudWatch Logs |

Set task CPU and memory for the selected Fargate size. Create/choose an ECS task execution role with ECR image-pull and CloudWatch Logs permissions. Use a task role only if the application later needs AWS API access.

For the API container, add environment variables:

| Name | Value |
| --- | --- |
| `DB_HOST` | RDS endpoint hostname |
| `DB_PORT` | `3306` |
| `DB_NAME` | `appdb` |

Set `DB_USER` and `DB_PASSWORD` using **Secrets** in the container definition, referencing Secrets Manager or SSM Parameter Store values. Grant the task execution role permission to retrieve those secrets, and configure the KMS permission too if using a customer-managed key. Do not place production credentials in plain-text task environment definitions.

No database variables are needed by the web task. Both images include container health checks; configure ECS health-check grace periods so API startup can retry a temporarily unavailable database.

### 6. Create ALB and target groups

1. Create an **internet-facing Application Load Balancer** in at least two public subnets and attach the ALB security group.
2. Create two target groups with target type **IP** (required for Fargate `awsvpc` tasks), protocol **HTTP**, and the same VPC:
   - Web target group: port `8080`, health-check path `/health`.
   - API target group: port `8000`, health-check path `/health`.
3. Set the health-check success code to `200`. The API `/health` is intentionally process health; use `/api/health/db` for an optional diagnostic, rather than making ALB health depend on a brief database interruption.

### 7. Create two ECS services

Create one service per task definition in the cluster:

- **Web service**: desired count at least 1, Fargate, private application subnets, public IP disabled, Web ECS SG, attach to the Web target group on port 8080.
- **API service**: desired count at least 1, Fargate, private application subnets, public IP disabled, API ECS SG, attach to the API target group on port 8000.

Enable deployment rollback/circuit breaker if available. Verify tasks become healthy in their target groups. Ensure the private subnets have outbound connectivity for ECR and CloudWatch Logs.

### 8. Configure ALB listener rules

Create an HTTP listener on port 80 with these ordered rules:

1. Path pattern `/api/*` → forward to the API target group.
2. Default rule (`/*`) → forward to the Web target group.

The browser calls the same-origin path `/api/products`; the ALB routes it to the API service. The API is not exposed on its own public endpoint. For production deployments, add HTTPS with an ACM certificate and redirect HTTP to HTTPS.

### 9. Test the deployment

Open the ALB DNS name in a browser. Add, list, and delete a product. Also verify:

```text
http://<ALB-DNS-NAME>/health
http://<ALB-DNS-NAME>/api/products
http://<ALB-DNS-NAME>/api/health/db
```

The first URL checks the web target. The second lists products through the API target. The database diagnostic returns HTTP 503 if the API cannot reach RDS. If requests fail, check target health, ECS task logs, subnet routes, security-group references, RDS credentials in the task definition, and the RDS endpoint value.

## Operational notes

- Keep application and database credentials out of Git and image layers; rotate them through Secrets Manager/SSM and redeploy tasks after secret changes.
- The included health endpoints and service logs help diagnose failures. Database errors return a generic HTTP 503 to clients while logging diagnostic details server-side.
- The web UI uses the browser's default USD currency display. Change the currency in `web/app.js` if needed.
- Pin image tags to a release version or digest for controlled production rollouts instead of reusing `latest`.
