# Prozpr — AWS EC2 Deployment Guide

Complete setup guide for deploying Prozpr (frontend + backend) on AWS EC2 using CodeDeploy + GitHub Actions.

---

## Architecture Overview

```
GitHub (push to main)
    │
    ▼
GitHub Actions → triggers AWS CodeDeploy
    │
    ▼
CodeDeploy Agent (on EC2) pulls code from GitHub
    │
    ▼
EC2 Instance runs Docker Compose
    ├── prozpr-frontend (Nginx on port 80)
    └── prozpr-backend  (Uvicorn on port 8000)
```

---

## Step 1: Launch an EC2 Instance

1. **AMI**: Amazon Linux 2023 (or Amazon Linux 2)
2. **Instance type**: `t3.small` minimum (Docker builds need ~2 GB RAM)
3. **Storage**: 20 GB gp3 minimum
4. **Security Group** — open these ports:
   | Port | Protocol | Source | Purpose |
   |------|----------|--------|---------|
   | 22   | TCP      | Your IP | SSH access |
   | 80   | TCP      | 0.0.0.0/0 | HTTP (frontend) |
   | 443  | TCP      | 0.0.0.0/0 | HTTPS (if using SSL) |
5. **Key pair**: Create or select an existing one for SSH access.

---

## Step 2: Create an IAM Role for EC2

1. Go to **IAM → Roles → Create Role**
2. **Trusted entity**: AWS Service → EC2
3. Attach these policies:
   - `AmazonEC2RoleforAWSCodeDeploy`
   - `AmazonSSMManagedInstanceCore` (optional, for SSM access)
4. Name it: `Prozpr-EC2-CodeDeploy-Role`
5. **Attach the role** to your EC2 instance:
   - EC2 Console → select instance → Actions → Security → Modify IAM role

---

## Step 3: Create an IAM User for GitHub Actions

1. Go to **IAM → Users → Create User**
2. Name: `prozpr-github-deployer`
3. Attach policy: `AWSCodeDeployDeployerAccess` (or a custom policy — see below)
4. Create an **Access Key** (CLI type) and save the credentials

### Custom policy (least privilege):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "codedeploy:CreateDeployment",
        "codedeploy:GetDeployment",
        "codedeploy:GetDeploymentConfig",
        "codedeploy:GetApplicationRevision",
        "codedeploy:RegisterApplicationRevision"
      ],
      "Resource": "*"
    }
  ]
}
```

---

## Step 4: Install CodeDeploy Agent on EC2

SSH into your instance and run:

```bash
# Amazon Linux 2023 / Amazon Linux 2
sudo yum update -y
sudo yum install -y ruby wget

cd /home/ec2-user
wget https://aws-codedeploy-ap-south-1.s3.ap-south-1.amazonaws.com/latest/install
chmod +x ./install
sudo ./install auto

# Verify it's running
sudo systemctl status codedeploy-agent
```

> **Note**: Replace `ap-south-1` with your region if different.

---

## Step 5: Create CodeDeploy Application

### Via AWS Console:

1. Go to **CodeDeploy → Applications → Create Application**
   - Name: `Prozpr`
   - Platform: `EC2/On-premises`

2. **Create Deployment Group**:
   - Name: `Prozpr-EC2`
   - Service role: Create a new IAM role with `AWSCodeDeployRole` policy
   - Deployment type: `In-place`
   - Environment config: `Amazon EC2 instances`
     - Tag key: `Name`, Value: your instance name
   - Deployment settings: `CodeDeployDefault.AllAtOnce`
   - **Uncheck** "Enable load balancer" (unless you have one)

### Via AWS CLI:

```bash
# Create the application
aws deploy create-application \
  --application-name Prozpr \
  --compute-platform Server

# Create the deployment group
aws deploy create-deployment-group \
  --application-name Prozpr \
  --deployment-group-name Prozpr-EC2 \
  --deployment-config-name CodeDeployDefault.AllAtOnce \
  --ec2-tag-filters Key=Name,Value=YOUR_INSTANCE_NAME,Type=KEY_AND_VALUE \
  --service-role-arn arn:aws:iam::YOUR_ACCOUNT_ID:role/CodeDeployServiceRole
```

---

## Step 6: Set Up Environment Variables on EC2

SSH into your instance and create the backend `.env` file:

```bash
sudo nano /home/ec2-user/.env.prozpr
```

Paste your production values (see `.env.production.example` in the repo root for the template). The CodeDeploy `after_install.sh` script will copy this file into the deployment directory automatically.

---

## Step 7: Add GitHub Secrets

In your GitHub repo, go to **Settings → Secrets and variables → Actions** and add:

| Secret Name | Value |
|-------------|-------|
| `AWS_ACCESS_KEY_ID` | From Step 3 |
| `AWS_SECRET_ACCESS_KEY` | From Step 3 |

---

## Step 8: Deploy

Push to `main` and the GitHub Actions workflow will:

1. Trigger a CodeDeploy deployment
2. CodeDeploy agent on EC2 pulls the code from GitHub
3. Runs `before_install.sh` — installs Docker, stops old containers
4. Copies files to `/home/ec2-user/prozpr/`
5. Runs `after_install.sh` — sets permissions, copies `.env`
6. Runs `start_application.sh` — builds Docker images, starts containers
7. Runs `validate_service.sh` — checks HTTP 200 on both frontend and backend

You can also trigger a manual deployment from **Actions → Deploy to AWS EC2 → Run workflow**.

---

## Troubleshooting

### Check deployment status
```bash
# On EC2 — CodeDeploy agent logs
sudo tail -f /var/log/aws/codedeploy-agent/codedeploy-agent.log

# Deployment script logs
sudo tail -f /opt/codedeploy-agent/deployment-root/deployment-logs/codedeploy-agent-deployments.log
```

### Check Docker containers
```bash
cd /home/ec2-user/prozpr
docker compose ps
docker compose logs backend --tail 100
docker compose logs frontend --tail 100
```

### Common issues

| Symptom | Fix |
|---------|-----|
| Deployment stuck "In Progress" | Check CodeDeploy agent: `sudo systemctl status codedeploy-agent` |
| "No instances matched" | Verify EC2 instance tags match the deployment group filter |
| "before_install.sh: Permission denied" | Scripts need LF line endings (not CRLF) — see below |
| Backend container unhealthy | Check `docker compose logs backend` — likely missing `.env` |
| Frontend shows blank page | Check `docker compose logs frontend` and verify VITE_API_URL |

### Fix Windows line endings (CRLF → LF)

Since you're developing on Windows, shell scripts may have CRLF line endings which will fail on Linux. Add this to your repo:

```bash
# Run once to fix all .sh files
git config core.autocrlf input
```

Or add a `.gitattributes` file (already included in this repo).

---

## Region Configuration

The GitHub Actions workflow defaults to `ap-south-1` (Mumbai). To change the region, update the `AWS_REGION` env var in `.github/workflows/deploy.yml`.

Make sure the CodeDeploy agent install URL also matches your region (Step 4).
