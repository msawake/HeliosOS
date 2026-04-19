#!/bin/bash
# ForgeOS API Interaction Demo
# This script demonstrates how to interact with ForgeOS via REST API

set -e

API_URL="http://localhost:5000"
BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

echo -e "${BOLD}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║                                                                              ║${NC}"
echo -e "${BOLD}║                     ForgeOS REST API Demo                                    ║${NC}"
echo -e "${BOLD}║                                                                              ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"

# 1. Check Platform Health
echo -e "\n${BLUE}${BOLD}1. Checking Platform Health${NC}"
echo -e "${YELLOW}GET $API_URL/api/health${NC}"
curl -s $API_URL/api/health | jq .
sleep 1

# 2. List Current Agents
echo -e "\n${BLUE}${BOLD}2. Listing Current Agents${NC}"
echo -e "${YELLOW}GET $API_URL/api/platform/agents${NC}"
AGENT_COUNT=$(curl -s $API_URL/api/platform/agents | jq 'length')
echo -e "${GREEN}Found $AGENT_COUNT agents${NC}"
curl -s $API_URL/api/platform/agents | jq '.[] | {name, stack, execution_type}'
sleep 1

# 3. Create a Chat Agent
echo -e "\n${BLUE}${BOLD}3. Creating a Chat Agent (Reflex)${NC}"
echo -e "${YELLOW}POST $API_URL/api/platform/agents${NC}"
CHAT_AGENT=$(curl -s -X POST $API_URL/api/platform/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "api-chat-bot",
    "stack": "forgeos",
    "execution_type": "reflex",
    "description": "Chat bot created via API demo",
    "department": "demo",
    "chat_model": "gpt-4o",
    "provider": "openai",
    "tools": ["mcp__filesystem__*"],
    "system_prompt": "You are a helpful chat assistant created via the ForgeOS REST API. Be friendly and concise."
  }')
echo $CHAT_AGENT | jq .
CHAT_AGENT_ID=$(echo $CHAT_AGENT | jq -r '.agent_id')
echo -e "${GREEN}✓ Created agent: $CHAT_AGENT_ID${NC}"
sleep 1

# 4. Create a Scheduled Agent
echo -e "\n${BLUE}${BOLD}4. Creating a Scheduled Agent${NC}"
echo -e "${YELLOW}POST $API_URL/api/platform/agents${NC}"
SCHEDULED_AGENT=$(curl -s -X POST $API_URL/api/platform/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "api-scheduler",
    "stack": "forgeos",
    "execution_type": "scheduled",
    "schedule": "0 */6 * * *",
    "description": "Runs every 6 hours",
    "department": "demo",
    "chat_model": "claude-sonnet-4-5-20250514",
    "provider": "anthropic",
    "tools": ["mcp__filesystem__*"],
    "system_prompt": "You are a scheduled task agent that runs every 6 hours."
  }')
echo $SCHEDULED_AGENT | jq .
SCHEDULED_AGENT_ID=$(echo $SCHEDULED_AGENT | jq -r '.agent_id')
echo -e "${GREEN}✓ Created agent: $SCHEDULED_AGENT_ID${NC}"
sleep 1

# 5. Get Agent Details
echo -e "\n${BLUE}${BOLD}5. Getting Agent Details${NC}"
echo -e "${YELLOW}GET $API_URL/api/platform/agents/$CHAT_AGENT_ID${NC}"
curl -s $API_URL/api/platform/agents/$CHAT_AGENT_ID | jq '{name, stack, execution_type, description, department, tools}'
sleep 1

# 6. Invoke the Chat Agent
echo -e "\n${BLUE}${BOLD}6. Invoking Chat Agent${NC}"
echo -e "${YELLOW}POST $API_URL/api/platform/agents/$CHAT_AGENT_ID/invoke${NC}"
INVOKE_RESULT=$(curl -s -X POST $API_URL/api/platform/agents/$CHAT_AGENT_ID/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Hello! Tell me about yourself in one sentence.",
    "context": {"demo": "api_interaction"}
  }')
echo $INVOKE_RESULT | jq '{status, result, tokens_used, cost_usd}'
echo -e "${GREEN}✓ Agent response: $(echo $INVOKE_RESULT | jq -r '.result')${NC}"
sleep 1

# 7. Platform Overview
echo -e "\n${BLUE}${BOLD}7. Platform Overview${NC}"
echo -e "${YELLOW}GET $API_URL/api/platform/overview${NC}"
curl -s $API_URL/api/platform/overview | jq .
sleep 1

# 8. List Agents by Filter
echo -e "\n${BLUE}${BOLD}8. Filtering Agents by Execution Type${NC}"
echo -e "${YELLOW}GET $API_URL/api/platform/agents?execution_type=reflex${NC}"
curl -s "$API_URL/api/platform/agents?execution_type=reflex" | jq '.[] | {name, execution_type}'
sleep 1

# 9. Check Scheduler Status
echo -e "\n${BLUE}${BOLD}9. Scheduler Status${NC}"
echo -e "${YELLOW}GET $API_URL/api/platform/scheduler${NC}"
curl -s $API_URL/api/platform/scheduler | jq '.[:3]'
sleep 1

# 10. Admin Metrics
echo -e "\n${BLUE}${BOLD}10. Admin Metrics${NC}"
echo -e "${YELLOW}GET $API_URL/api/admin/metrics${NC}"
curl -s $API_URL/api/admin/metrics | jq '{agents, usage, timestamp}'
sleep 1

# 11. Delete an Agent
echo -e "\n${BLUE}${BOLD}11. Deleting Agent${NC}"
echo -e "${YELLOW}DELETE $API_URL/api/platform/agents/$SCHEDULED_AGENT_ID${NC}"
curl -s -X DELETE $API_URL/api/platform/agents/$SCHEDULED_AGENT_ID
echo -e "${GREEN}✓ Deleted agent: $SCHEDULED_AGENT_ID${NC}"
sleep 1

# 12. Final Agent Count
echo -e "\n${BLUE}${BOLD}12. Final Agent Count${NC}"
FINAL_COUNT=$(curl -s $API_URL/api/platform/agents | jq 'length')
echo -e "${GREEN}Total agents: $FINAL_COUNT${NC}"

echo -e "\n${BOLD}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║                                                                              ║${NC}"
echo -e "${BOLD}║                     ✅ API Demo Complete!                                     ║${NC}"
echo -e "${BOLD}║                                                                              ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"

echo -e "\n${YELLOW}Next steps:${NC}"
echo "• View API docs: $API_URL/docs"
echo "• List agents: curl $API_URL/api/platform/agents | jq ."
echo "• Invoke agent: curl -X POST $API_URL/api/platform/agents/$CHAT_AGENT_ID/invoke -H 'Content-Type: application/json' -d '{\"prompt\": \"Hello!\"}'"
echo "• Read guide: cat TERMINAL_USAGE_GUIDE.md"
