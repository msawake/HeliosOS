#!/usr/bin/env python3
"""
ForgeOS Terminal Quickstart Example

This script demonstrates all three ways to interact with ForgeOS from the terminal:
1. Python SDK (programmatic)
2. CLI commands (via subprocess)
3. REST API (via requests)

Usage:
    PYTHONPATH=. python examples/terminal_quickstart.py
"""

import sys
import json
import subprocess
from pathlib import Path

sys.path.insert(0, ".")

from src.forgeos_sdk import Agent, ForgeOSClient, ForgeOSError


def print_section(title: str):
    """Print a formatted section header."""
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}\n")


def method_1_python_sdk():
    """Method 1: Using the Python SDK directly."""
    print_section("METHOD 1: Python SDK")
    
    # Create agent using builder pattern
    manifest = (Agent.builder("quickstart-sdk")
        .forgeos()
        .reflex()
        .model("gpt-4o", provider="openai")
        .tools("mcp__filesystem__*")
        .prompt("You are a helpful assistant for the ForgeOS quickstart example.")
        .description("Example agent created via Python SDK")
        .department("examples")
        .build())
    
    print("📦 Created agent manifest:")
    print(f"   Name: {manifest.metadata.name}")
    print(f"   Stack: {manifest.spec.stack}")
    print(f"   Type: {manifest.spec.execution_type}")
    print(f"   Model: {manifest.spec.llm.chat_model}")
    
    # Deploy and invoke
    with ForgeOSClient(base_url="http://localhost:5000") as client:
        try:
            # Deploy
            print("\n🚀 Deploying agent...")
            agent_id = client.deploy(manifest)
            print(f"✅ Deployed: {agent_id}")
            
        except ForgeOSError as e:
            if "already exists" in str(e):
                print("⚠️  Agent already exists, using existing one")
                agents = client.list()
                agent_id = next(
                    (a["agent_id"] for a in agents if a["name"] == "quickstart-sdk"),
                    "quickstart-sdk"
                )
            else:
                raise
        
        # Invoke
        print("\n💬 Invoking agent...")
        result = client.invoke(agent_id, "Say hello and explain what you are!")
        
        print(f"✅ Status: {result.get('status')}")
        print(f"📝 Response: {result.get('result', '')[:200]}...")
        print(f"🔢 Tokens: {result.get('tokens_used', 0)}")
        print(f"💰 Cost: ${result.get('cost_usd', 0):.4f}")


def method_2_cli():
    """Method 2: Using the CLI tool."""
    print_section("METHOD 2: CLI Tool (forgeos command)")
    
    # Create a temporary YAML file
    yaml_content = """apiVersion: forgeos/v1
kind: Agent
metadata:
  name: quickstart-cli
  description: Example agent created via CLI
  department: examples

spec:
  stack: forgeos
  execution_type: reflex
  
  llm:
    chat_model: gpt-4o
    provider: openai
  
  tools:
    - mcp__filesystem__*
  
  system_prompt: |
    You are a helpful assistant for the ForgeOS CLI quickstart example.
"""
    
    yaml_file = Path("/tmp/quickstart-cli.yaml")
    yaml_file.write_text(yaml_content)
    print(f"📄 Created manifest: {yaml_file}")
    
    # Deploy using CLI
    print("\n🚀 Deploying via CLI...")
    try:
        result = subprocess.run(
            ["forgeos", "deploy", str(yaml_file)],
            capture_output=True,
            text=True,
            check=True
        )
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        if "already exists" in e.stderr:
            print("⚠️  Agent already exists")
        else:
            print(f"❌ Error: {e.stderr}")
    
    # List agents using CLI
    print("\n📋 Listing agents via CLI...")
    try:
        result = subprocess.run(
            ["forgeos", "list"],
            capture_output=True,
            text=True,
            check=True
        )
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"❌ Error: {e.stderr}")
    
    # Invoke using CLI
    print("\n💬 Invoking via CLI...")
    try:
        result = subprocess.run(
            ["forgeos", "invoke", "quickstart-cli", "Say hello and explain what you are!"],
            capture_output=True,
            text=True,
            check=True
        )
        response = json.loads(result.stdout)
        print(f"✅ Status: {response.get('status')}")
        print(f"📝 Response: {response.get('result', '')[:200]}...")
    except subprocess.CalledProcessError as e:
        print(f"❌ Error: {e.stderr}")
    except json.JSONDecodeError:
        print(f"⚠️  Could not parse response")


def method_3_rest_api():
    """Method 3: Using the REST API with curl."""
    print_section("METHOD 3: REST API (cURL)")
    
    # Deploy agent
    print("🚀 Deploying via REST API...")
    deploy_cmd = [
        "curl", "-s", "-X", "POST",
        "http://localhost:5000/api/platform/agents",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({
            "name": "quickstart-api",
            "stack": "forgeos",
            "execution_type": "reflex",
            "description": "Example agent created via REST API",
            "department": "examples",
            "chat_model": "gpt-4o",
            "provider": "openai",
            "tools": ["mcp__filesystem__*"],
            "system_prompt": "You are a helpful assistant for the ForgeOS REST API quickstart example."
        })
    ]
    
    try:
        result = subprocess.run(deploy_cmd, capture_output=True, text=True, check=True)
        response = json.loads(result.stdout)
        print(f"✅ Deployed: {response.get('agent_id', response.get('name'))}")
    except subprocess.CalledProcessError as e:
        print(f"⚠️  {e.stderr}")
    except json.JSONDecodeError:
        print(f"⚠️  Could not parse response")
    
    # List agents
    print("\n📋 Listing agents via REST API...")
    list_cmd = ["curl", "-s", "http://localhost:5000/api/platform/agents"]
    
    try:
        result = subprocess.run(list_cmd, capture_output=True, text=True, check=True)
        agents = json.loads(result.stdout)
        print(f"✅ Found {len(agents)} agents:")
        for agent in agents[:5]:  # Show first 5
            print(f"   - {agent.get('name')} ({agent.get('stack')}, {agent.get('execution_type')})")
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"❌ Error: {e}")
    
    # Invoke agent
    print("\n💬 Invoking via REST API...")
    invoke_cmd = [
        "curl", "-s", "-X", "POST",
        "http://localhost:5000/api/platform/agents/quickstart-api/invoke",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({
            "prompt": "Say hello and explain what you are!"
        })
    ]
    
    try:
        result = subprocess.run(invoke_cmd, capture_output=True, text=True, check=True)
        response = json.loads(result.stdout)
        print(f"✅ Status: {response.get('status')}")
        print(f"📝 Response: {response.get('result', '')[:200]}...")
        print(f"🔢 Tokens: {response.get('tokens_used', 0)}")
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"❌ Error: {e}")


def check_platform():
    """Check if the platform is running."""
    print_section("Platform Health Check")
    
    try:
        result = subprocess.run(
            ["curl", "-s", "http://localhost:5000/api/health"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        health = json.loads(result.stdout)
        print(f"✅ Platform is running")
        print(f"   Status: {health.get('status')}")
        if 'components' in health:
            print(f"   Agents: {health['components'].get('agents_registered', 0)}")
            print(f"   Providers: {', '.join(health['components'].get('llm_providers', []))}")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
        print("❌ Platform is not running!")
        print("\n💡 Start the platform with:")
        print("   PYTHONPATH=. python3 -m src.bootstrap --dashboard --no-auth --port 5000")
        return False


def main():
    """Run all examples."""
    print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║                   ForgeOS Terminal Quickstart Examples                      ║
║                                                                              ║
║  This script demonstrates three ways to use ForgeOS from the terminal:      ║
║  1. Python SDK (programmatic)                                               ║
║  2. CLI tool (forgeos command)                                              ║
║  3. REST API (cURL)                                                         ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
    """)
    
    # Check if platform is running
    if not check_platform():
        return 1
    
    # Run examples
    try:
        method_1_python_sdk()
        method_2_cli()
        method_3_rest_api()
        
        print_section("✅ All Examples Complete!")
        print("""
Next steps:
1. Check the deployed agents: forgeos list
2. View API docs: http://localhost:5000/docs
3. Read QUICK_REFERENCE.md for more examples
4. Read TERMINAL_USAGE_GUIDE.md for detailed docs
        """)
        
        return 0
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        return 130
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
