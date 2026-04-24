# Email Sending Setup Guide for ForgeOS

This guide shows you how to configure email sending in ForgeOS using MCP servers.

---

## 📧 Available Email Solutions

ForgeOS supports multiple email MCP servers:

### 1. **Resend API** (Recommended - Easiest)
- **Package**: `@toolsdk.ai/mcp-send-email`
- **Provider**: Resend.com
- **Setup Time**: 5 minutes
- **Cost**: Free tier available (100 emails/day)
- **Best For**: Transactional emails, notifications

### 2. **SMTP Email Server**
- **Package**: `mcp-server-email`
- **Provider**: Any SMTP server (Gmail, SendGrid, Mailgun, etc.)
- **Setup Time**: 10 minutes
- **Cost**: Depends on provider
- **Best For**: Using existing email infrastructure

### 3. **Gmail API**
- **Package**: `gmail` or `gmail-mcp`
- **Provider**: Google Gmail
- **Setup Time**: 15 minutes (OAuth setup)
- **Cost**: Free
- **Best For**: Personal Gmail accounts, reading + sending

---

## 🚀 Quick Setup: Resend API (Recommended)

### Step 1: Get Resend API Key

1. Go to https://resend.com
2. Sign up for a free account
3. Navigate to API Keys
4. Create a new API key
5. Copy the key (starts with `re_`)

### Step 2: Add to ForgeOS Environment

```bash
# Add to .env file
echo 'RESEND_API_KEY=re_your_api_key_here' >> .env
echo 'RESEND_FROM_EMAIL=onboarding@resend.dev' >> .env  # Or your verified domain
```

### Step 3: Install Resend MCP Server

```bash
# Install the MCP server package
npm install -g @toolsdk.ai/mcp-send-email

# Or using the ForgeOS API
curl -X POST http://localhost:5000/api/clients/default/mcp-servers \
  -H "Content-Type: application/json" \
  -d '{
    "server_name": "resend-email",
    "package": "@toolsdk.ai/mcp-send-email",
    "env_vars": {
      "RESEND_API_KEY": "re_your_api_key_here"
    },
    "args": []
  }'
```

### Step 4: Create an Email Agent

```bash
curl -X POST http://localhost:5000/api/platform/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "email-sender",
    "stack": "forgeos",
    "execution_type": "reflex",
    "description": "Agent that can send emails via Resend",
    "department": "communications",
    "chat_model": "gpt-4o",
    "provider": "openai",
    "tools": ["mcp__resend-email__*"],
    "system_prompt": "You are an email sending agent. When asked to send an email, use the available tools to send it via Resend API. Always confirm the recipient, subject, and content before sending."
  }'
```

### Step 5: Send an Email

```bash
# Get the agent ID from the response above, then:
curl -X POST http://localhost:5000/api/platform/agents/<agent-id>/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Send an email to jamartinezaguilar@gmail.com with subject \"Hello from ForgeOS\" and message \"This is a test email sent from ForgeOS using the Resend API!\""
  }'
```

---

## 🔧 Alternative Setup: SMTP Email

### Step 1: Get SMTP Credentials

For Gmail:
1. Enable 2FA on your Google account
2. Generate an App Password: https://myaccount.google.com/apppasswords
3. Use these credentials:
   - SMTP Server: `smtp.gmail.com`
   - Port: `587` (TLS) or `465` (SSL)
   - Username: Your Gmail address
   - Password: App Password (16 characters)

### Step 2: Configure Environment

```bash
# Add to .env
echo 'SMTP_HOST=smtp.gmail.com' >> .env
echo 'SMTP_PORT=587' >> .env
echo 'SMTP_USER=your-email@gmail.com' >> .env
echo 'SMTP_PASSWORD=your-app-password' >> .env
echo 'SMTP_FROM=your-email@gmail.com' >> .env
```

### Step 3: Install SMTP MCP Server

```bash
# Install Python package
pip install mcp-server-email

# Configure in ForgeOS
curl -X POST http://localhost:5000/api/clients/default/mcp-servers \
  -H "Content-Type: application/json" \
  -d '{
    "server_name": "smtp-email",
    "package": "mcp-server-email",
    "env_vars": {
      "SMTP_HOST": "smtp.gmail.com",
      "SMTP_PORT": "587",
      "SMTP_USER": "your-email@gmail.com",
      "SMTP_PASSWORD": "your-app-password",
      "SMTP_FROM": "your-email@gmail.com"
    },
    "args": []
  }'
```

### Step 4: Create Email Agent

```bash
curl -X POST http://localhost:5000/api/platform/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "smtp-email-sender",
    "stack": "forgeos",
    "execution_type": "reflex",
    "description": "Agent that sends emails via SMTP",
    "department": "communications",
    "chat_model": "gpt-4o",
    "provider": "openai",
    "tools": ["mcp__smtp-email__*"],
    "system_prompt": "You are an email sending agent using SMTP. Send emails when requested."
  }'
```

---

## 🎯 Python SDK Example

```python
from forgeos_sdk import Agent, ForgeOSClient

# Create email agent
manifest = (Agent.builder("email-agent")
    .forgeos()
    .reflex()
    .model("gpt-4o")
    .tools("mcp__resend-email__*")  # Or mcp__smtp-email__*
    .prompt("""
        You are an email sending agent. When asked to send an email:
        1. Confirm the recipient email address
        2. Confirm the subject line
        3. Confirm the message content
        4. Use the send_email tool to send it
        5. Report success or failure
    """)
    .description("Sends emails via Resend API")
    .build())

# Deploy and send email
with ForgeOSClient() as client:
    agent_id = client.deploy(manifest)
    
    result = client.invoke(
        agent_id,
        "Send an email to jamartinezaguilar@gmail.com with subject 'Hello from ForgeOS' "
        "and message 'This is a test email from the ForgeOS Python SDK!'"
    )
    
    print(result["result"])
```

---

## 📋 Email Agent YAML Manifest

```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: email-sender
  description: Sends emails via Resend API
  department: communications

spec:
  stack: forgeos
  execution_type: reflex
  
  llm:
    chat_model: gpt-4o
    provider: openai
  
  tools:
    - mcp__resend-email__send_email
    - mcp__resend-email__schedule_email
  
  system_prompt: |
    You are an email sending agent with access to the Resend API.
    
    When asked to send an email:
    1. Extract the recipient email address
    2. Extract or create the subject line
    3. Extract or create the message content
    4. Use the send_email tool with these parameters:
       - to: recipient email
       - subject: email subject
       - text: email body (plain text)
       - from: sender email (use default if not specified)
    5. Confirm successful sending
    
    Always be helpful and confirm details before sending.
```

---

## 🔍 Checking Available Email Tools

```bash
# List all MCP servers
curl http://localhost:5000/api/clients/default/mcp-servers | jq .

# Search for email-related MCP packages
curl http://localhost:5000/api/mcps/search?query=email | jq .

# Get details of a specific MCP package
curl http://localhost:5000/api/mcps/resend-email | jq .
```

---

## 🧪 Testing Email Functionality

### Test 1: Simple Email

```bash
curl -X POST http://localhost:5000/api/platform/agents/<agent-id>/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Send a test email to jamartinezaguilar@gmail.com saying Hello!"
  }'
```

### Test 2: Formatted Email

```bash
curl -X POST http://localhost:5000/api/platform/agents/<agent-id>/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Send an email to jamartinezaguilar@gmail.com with subject \"ForgeOS Test\" and this message: \"Hi! This is a test email from ForgeOS. The platform is working great!\""
  }'
```

### Test 3: Scheduled Email (if supported)

```bash
curl -X POST http://localhost:5000/api/platform/agents/<agent-id>/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Schedule an email to jamartinezaguilar@gmail.com for tomorrow at 9 AM with subject \"Reminder\" and message \"This is your scheduled reminder!\""
  }'
```

---

## 🛠️ Troubleshooting

### Issue: MCP Server Not Found

```bash
# Check if MCP server is installed
curl http://localhost:5000/api/clients/default/mcp-servers | jq '.[] | select(.server_name == "resend-email")'

# If not found, install it
curl -X POST http://localhost:5000/api/clients/default/mcp-servers \
  -H "Content-Type: application/json" \
  -d '{
    "server_name": "resend-email",
    "package": "@toolsdk.ai/mcp-send-email",
    "env_vars": {"RESEND_API_KEY": "re_..."},
    "args": []
  }'
```

### Issue: Authentication Failed

```bash
# Verify environment variables
echo $RESEND_API_KEY
echo $SMTP_PASSWORD

# Update MCP server config
curl -X PUT http://localhost:5000/api/clients/default/mcp-servers/resend-email \
  -H "Content-Type: application/json" \
  -d '{
    "env_vars": {"RESEND_API_KEY": "re_new_key_here"}
  }'
```

### Issue: Email Not Sending

1. Check agent has the correct tools:
   ```bash
   curl http://localhost:5000/api/platform/agents/<agent-id> | jq '.tools'
   ```

2. Check MCP server is running:
   ```bash
   curl http://localhost:5000/api/admin/health | jq '.mcp_servers'
   ```

3. Check agent invocation logs:
   ```bash
   curl http://localhost:5000/api/admin/metrics | jq '.audit'
   ```

---

## 📚 Email Provider Comparison

| Provider | Setup Difficulty | Cost | Rate Limits | Best For |
|----------|-----------------|------|-------------|----------|
| **Resend** | ⭐ Easy | Free tier: 100/day | 100 emails/day (free) | Quick setup, testing |
| **Gmail SMTP** | ⭐⭐ Medium | Free | 500/day | Personal use |
| **SendGrid** | ⭐⭐ Medium | Free tier: 100/day | 100 emails/day (free) | Production apps |
| **Mailgun** | ⭐⭐ Medium | Pay as you go | Varies | High volume |
| **AWS SES** | ⭐⭐⭐ Hard | Very cheap | 62,000/month (free tier) | Enterprise |

---

## 🎯 Recommended Setup for Your Use Case

For sending an email to `jamartinezaguilar@gmail.com`, I recommend:

### Option 1: Resend (Fastest)
1. Sign up at https://resend.com (2 minutes)
2. Get API key
3. Add to `.env`: `RESEND_API_KEY=re_...`
4. Create email agent (see above)
5. Send email via API

### Option 2: Gmail SMTP (Free, No Signup)
1. Use your existing Gmail account
2. Generate App Password
3. Configure SMTP settings
4. Create email agent
5. Send email via API

---

## 🚀 Next Steps

1. **Choose a provider** (Resend recommended for quick start)
2. **Get credentials** (API key or SMTP password)
3. **Configure MCP server** (via API or config file)
4. **Create email agent** (using examples above)
5. **Send test email** to verify it works
6. **Integrate into workflows** (scheduled reports, alerts, etc.)

---

## 📞 Support

If you need help setting up email sending:
1. Check the MCP server documentation
2. Review ForgeOS logs: `tail -f forgeos.log`
3. Test with curl commands first
4. Verify environment variables are set correctly

---

**Ready to send emails?** Start with the Resend setup above - it's the fastest way to get email working in ForgeOS!
