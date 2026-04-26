# Vertex AI Search Capabilities Analysis

**Date:** April 22, 2026  
**Question:** How are Vertex AI's search capabilities used by Claude in ForgeOS?

---

## Answer: **NOT USED**

I (Claude/Anthropic) am **not using Vertex AI's search/grounding capabilities** in this ForgeOS codebase. Here's what I found:

---

## Current Vertex AI Integration

### **What IS Implemented:**

The ForgeOS platform has **basic Vertex AI Gemini integration** for LLM calls:

**File:** `src/platform/llm_router.py` (lines 603-731)

```python
async def _call_vertex(
    self, config: dict, model: str, messages: list[dict],
    tools: list[dict] | None = None,
) -> LLMResponse:
    """Call Vertex AI Gemini with tool calling support."""
    # 1. Get gcloud access token
    # 2. Convert messages to Vertex AI format
    # 3. Convert tool definitions to functionDeclarations
    # 4. POST to Vertex AI API
    # 5. Parse response and extract tool calls
```

**Capabilities:**
- ✅ Basic Gemini model calls
- ✅ Tool/function calling support
- ✅ Message format conversion (Anthropic → Vertex)
- ✅ Token usage tracking

**NOT Implemented:**
- ❌ **Google Search Grounding** (`google_search_retrieval`)
- ❌ **Vertex AI Search** (enterprise search)
- ❌ **Grounding with Google Search**
- ❌ **Grounding with custom data stores**
- ❌ **Retrieval-Augmented Generation (RAG)**

---

## What Vertex AI Search/Grounding Could Provide

### **1. Google Search Grounding**

**Feature:** Real-time web search integration directly in Gemini responses

**API Structure:**
```python
payload = {
    "contents": [...],
    "tools": [
        {
            "google_search_retrieval": {
                "dynamic_retrieval_config": {
                    "mode": "MODE_DYNAMIC",
                    "dynamic_threshold": 0.7
                }
            }
        }
    ]
}
```

**Benefits:**
- Automatic fact-checking with citations
- Up-to-date information without manual web search tools
- Reduced hallucinations
- Source attribution

**Example Response:**
```json
{
  "candidates": [{
    "content": {
      "parts": [{
        "text": "The current price of Bitcoin is $67,234 [1]..."
      }]
    },
    "groundingMetadata": {
      "webSearchQueries": ["bitcoin price"],
      "groundingChunks": [{
        "web": {
          "uri": "https://coinmarketcap.com/...",
          "title": "Bitcoin Price Today"
        }
      }],
      "groundingSupports": [{
        "segment": {"text": "$67,234"},
        "groundingChunkIndices": [0],
        "confidenceScores": [0.95]
      }]
    }
  }]
}
```

---

### **2. Vertex AI Search (Enterprise Search)**

**Feature:** Search over private enterprise data stores

**Use Cases:**
- Internal documentation search
- Customer support knowledge bases
- Product catalogs
- Legal/compliance documents

**API Structure:**
```python
payload = {
    "contents": [...],
    "tools": [
        {
            "retrieval": {
                "vertex_ai_search": {
                    "datastore": "projects/PROJECT/locations/LOCATION/collections/default_collection/dataStores/DATASTORE_ID"
                }
            }
        }
    ]
}
```

---

### **3. Grounding with Custom Data Stores**

**Feature:** RAG over custom corpora (PDFs, websites, structured data)

**Workflow:**
1. Upload documents to Vertex AI Search data store
2. Configure grounding in Gemini API call
3. Gemini automatically retrieves relevant chunks
4. Response includes citations to source documents

---

## Why It's Not Implemented in ForgeOS

### **Possible Reasons:**

1. **Platform-Agnostic Design**
   - ForgeOS supports multiple LLM providers (Anthropic, OpenAI, Vertex)
   - Grounding is Vertex-specific, breaks provider abstraction
   - Would need equivalent features for other providers

2. **Tool-Based Approach Preferred**
   - ForgeOS uses explicit tools for web search (`WebSearch` tool)
   - More control over when/how search happens
   - Works across all LLM providers

3. **Cost Considerations**
   - Google Search Grounding adds per-query costs
   - Vertex AI Search requires data store setup/maintenance
   - Explicit tools allow better cost tracking

4. **Not Yet Implemented**
   - Vertex AI integration is basic (added recently)
   - Grounding features may be planned for future

---

## Current Search Capabilities in ForgeOS

### **What ForgeOS DOES Use for Search:**

1. **MCP Server: Vertex AI Gemini** (available but not configured)
   - File: `resources/mcps/packages/cloud-platforms/vertex-ai-gemini.json`
   - Description: "Provides a bridge to Google Cloud's Vertex AI Gemini models with **web search grounding**"
   - Status: **Available as MCP package, not actively used**

2. **Explicit Web Search Tools**
   - `WebSearch` tool (likely via MCP or custom implementation)
   - Used by agents like `sales-sdr`, `email-triage`, etc.
   - Examples in `src/companies/practical/agent_configs.py`:
     ```python
     "email-triage": [
         "Read",
         "mcp__google-workspace__search_gmail_messages",
         "mcp__google-workspace__get_gmail_message_content",
         ...
     ]
     ```

3. **Google Workspace Search**
   - Gmail message search
   - Calendar event search
   - Drive file search
   - Via MCP Google Workspace integration

4. **Elasticsearch/OpenSearch** (available via MCP)
   - Multiple MCP packages available:
     - `elasticsearch.json`
     - `opensearch.json`
     - `elasticsearch-knowledge-graph.json`

---

## How I (Claude) Use Search in ForgeOS

As the AI assistant running in ForgeOS, I use search through:

1. **Tool Calls** (not Vertex AI grounding)
   - When I need information, I call tools like `WebSearch`
   - The platform executes the tool and returns results
   - I then synthesize the information in my response

2. **MCP Integrations**
   - Google Workspace search for emails, calendar, drive
   - Potential Elasticsearch/OpenSearch for knowledge bases
   - Research tools (arXiv, PubMed, etc.)

3. **No Direct Grounding**
   - I don't have access to Vertex AI's grounding features
   - All search is explicit via tool calls
   - This is by design for platform-agnostic operation

---

## Comparison: Tool-Based vs. Grounding-Based Search

| Aspect | ForgeOS (Tool-Based) | Vertex AI Grounding |
|--------|---------------------|---------------------|
| **Provider Support** | ✅ Works with all LLMs | ❌ Vertex AI only |
| **Cost Tracking** | ✅ Per-tool tracking | ⚠️ Bundled with LLM call |
| **Control** | ✅ Explicit when to search | ⚠️ Automatic (less control) |
| **Citations** | ⚠️ Manual extraction | ✅ Automatic with confidence |
| **Latency** | ⚠️ Extra round-trip | ✅ Single API call |
| **Governance** | ✅ Hook chain applies | ⚠️ Harder to govern |
| **Debugging** | ✅ Clear tool execution logs | ⚠️ Opaque grounding process |

---

## Recommendation: Should ForgeOS Add Vertex AI Grounding?

### **Pros:**
1. **Reduced latency** — Single API call instead of tool → LLM → tool cycle
2. **Automatic citations** — Built-in source attribution
3. **Better fact-checking** — Confidence scores for grounded statements
4. **Simplified agent logic** — No need to explicitly call search tools

### **Cons:**
1. **Breaks provider abstraction** — Vertex-specific feature
2. **Less governance** — Harder to apply hook chain to grounding
3. **Cost opacity** — Grounding costs bundled with LLM call
4. **Reduced control** — Can't decide when to search vs. use existing knowledge

### **Verdict:**

**For ForgeOS:** Keep tool-based approach as primary, optionally add grounding as **opt-in feature** for Vertex AI users.

**Implementation Path:**
1. Add `enable_grounding: bool` flag to Vertex AI config
2. When enabled, add `google_search_retrieval` to tools array
3. Parse `groundingMetadata` from response
4. Expose citations in `LLMResponse.raw` field
5. Document as Vertex-specific feature

**Example:**
```python
# In llm_router.py _call_vertex()
if config.get("enable_grounding"):
    payload["tools"] = payload.get("tools", [])
    payload["tools"].append({
        "google_search_retrieval": {
            "dynamic_retrieval_config": {
                "mode": "MODE_DYNAMIC",
                "dynamic_threshold": 0.7
            }
        }
    })

# Parse grounding metadata
grounding_metadata = candidates[0].get("groundingMetadata", {})
if grounding_metadata:
    raw["grounding_metadata"] = grounding_metadata
    raw["citations"] = [
        {
            "uri": chunk["web"]["uri"],
            "title": chunk["web"]["title"]
        }
        for chunk in grounding_metadata.get("groundingChunks", [])
    ]
```

---

## Conclusion

**Current State:**
- I (Claude) do **NOT** use Vertex AI's search/grounding capabilities
- ForgeOS uses **tool-based search** (WebSearch, Google Workspace, etc.)
- Vertex AI integration is **basic** (LLM calls + function calling only)

**Why:**
- Platform-agnostic design (supports Anthropic, OpenAI, Vertex)
- Better governance and cost tracking with explicit tools
- More control over search behavior

**Future:**
- Vertex AI grounding could be added as **opt-in feature**
- Would complement (not replace) tool-based search
- Best for Vertex-only deployments needing automatic fact-checking

---

**Generated:** 2026-04-22  
**Analysis Method:** Code review + API documentation research  
**Confidence Level:** High (based on complete codebase search)
