# LangGraph API Update - March 3, 2026

## Overview
Updated LangGraph email ingestion and classification APIs to resolve crashes and improve reliability through robust timeout handling, error recovery, and configuration validation.

## Problem Summary
The LangGraph API was prone to crashes due to:
- **Hanging requests**: No timeout handling on async operations (HTTP calls, LLM requests)
- **Silent failures**: Inadequate error logging and recovery
- **Configuration issues**: No validation of required environment variables
- **Poor timeout defaults**: httpx timeouts without asyncio.wait_for() wrapper

## Solution Implemented

### Files Modified

#### 1. `src/agent/email_ingestion_graph.py`
**Added timeout constants** at the module level:
```python
HTTP_TIMEOUT = httpx.Timeout(30.0)
LLM_TIMEOUT = 60.0  # seconds
TOKEN_REQUEST_TIMEOUT = 30.0  # seconds
```

**Functions updated with timeout handling:**

1. **`get_access_token()`** 
   - Added asyncio.wait_for() wrapper for httpx requests
   - Timeout: 30 seconds
   - Catches asyncio.TimeoutError separately from other exceptions
   - Returns None on timeout instead of hanging

2. **`get_email_messages()`**
   - Added MAIL_USER configuration validation
   - Added asyncio.wait_for() wrapper (30s timeout)
   - Handles json.JSONDecodeError
   - Improved error messages with exception types

3. **`download_attachments()`**
   - Added MAIL_USER configuration validation  
   - Added asyncio.wait_for() wrapper (45s timeout)
   - Handles json.JSONDecodeError
   - Better logging with exception context

4. **`classify_email()`**
   - Added OPENROUTER_API_KEY validation
   - Added asyncio.wait_for() wrapper for LLM call (60s timeout)
   - Returns graceful fallback ("disqualify") on timeout
   - Logs exception types for debugging
   - Includes full traceback in error logging

#### 2. `src/agent/email_classification_graph.py`
**Added timeout constants:**
```python
WEB_SEARCH_TIMEOUT = 10.0  # seconds
LLM_TIMEOUT = 60.0  # seconds  
HTTP_TIMEOUT = httpx.Timeout(10.0)
```

**Functions updated with timeout handling:**

1. **`_fetch_public_company_context()`**
   - Added asyncio.wait_for() for DuckDuckGo requests (10s)
   - Added asyncio.wait_for() for Wikipedia requests (10s)
   - Separate timeout error handling for each source
   - Continues to try other sources if one times out
   - Better logging with exception types

2. **`classify_email()`**
   - Added OPENROUTER_API_KEY validation before LLM call
   - Added asyncio.wait_for() wrapper for LLM (60s timeout)
   - Returns graceful fallback on timeout/error
   - Logs exception types explicitly
   - Includes full traceback in error logging

## Key Improvements

### 1. Timeout Handling (CRITICAL)
✅ All async operations now have explicit timeout protection
✅ Prevents server hanging from slow/unresponsive API calls
✅ asyncio.wait_for() wrapper ensures timeout is enforced
✅ Separate handling for timeout vs other error types

### 2. Configuration Validation
✅ Validates required API keys before making requests
✅ Clear error messages indicating missing configuration
✅ Prevents cryptic failures from missing env vars
✅ Helps with debugging deployment issues

### 3. Error Recovery
✅ Email classification falls back to safe "disqualify" result
✅ Graph continues execution instead of crashing
✅ Error status is included in output for tracking
✅ No unhandled exceptions propagate

### 4. Logging Improvements
✅ Exception types logged (not just messages)
✅ Full tracebacks included for debugging
✅ Timeout errors logged separately
✅ More context in error messages

## Timeout Values Explained

| Operation | Timeout | Rationale |
|-----------|---------|-----------|
| Token request | 30s | Microsoft auth is usually fast |
| Email fetch | 30s | Usually completes quickly |
| Attachment download | 45s | Can be slower for large attachments |
| LLM classification | 60s | Models need time to process |
| Web search (DuckDuckGo) | 10s | Public web API, fail fast |
| Web search (Wikipedia) | 10s | Public web API, fail fast |

## Testing the Updates

### To start the API:
```bash
cd /Users/antonidebicki/Realize/Email\ Automation/antoni-test-system
langgraph dev
```

### Expected behavior:
- ✅ Server starts without hanging
- ✅ Both graphs load successfully (check http://localhost:2024/graphs)
- ✅ Email requests with timeouts return clean error messages
- ✅ No unhandled exceptions in logs
- ✅ Clear error messages for debugging

### Example error responses:
```json
{
  "status": "failed: email fetch timeout",
  "email_content": ""
}
```

```json
{
  "status": "email classification timed out after 60s; fallback returned",
  "classification": {
    "action": "disqualify",
    "company_type": "unknown",
    "confidence": 0.0,
    "error": "classification_failed: timeout"
  }
}
```

## Environment Variables Required

Ensure these are set in `.env`:
```
# OpenRouter LLM
OPENROUTER_API_KEY=sk_xxx...
OPENAI_BASE_URL=https://openrouter.ai/api/v1

# Microsoft Graph
CLIENT_ID=xxx...
CLIENT_SECRET=xxx...
TENANT_ID=xxx...
MAIL_USER=your-email@company.com
```

## Performance Impact

- **Network calls**: Now have defined timeouts, preventing indefinite hangs
- **Memory**: Unchanged (no buffering changes)
- **CPU**: Slightly improved (async operations complete predictably)
- **Overall latency**: Reduced (failed operations fail fast)

## Backward Compatibility

✅ **API contract unchanged**: All request/response formats remain the same
✅ **Graph signatures unchanged**: Input/output schemas are identical
✅ **Graceful fallbacks**: Timeout errors behave like other errors

## Deployment Checklist

- [ ] Review timeout values match your infrastructure (internet speed, API latency)
- [ ] Verify all required environment variables are set
- [ ] Test with production email data
- [ ] Monitor logs for timeouts in first 24 hours
- [ ] Adjust timeouts if needed (increase for slow networks, decrease for fast environments)

## Monitoring Recommendations

Monitor your logs for these patterns:
- `"failed: email fetch timeout"` - Email retrieval is slow
- `"email classification timed out after 60s"` - LLM is slow
- `"configuration missing"` - Deployment issue
- `"classification_failed"` - LLM or network error

If timeouts are frequent, consider:
1. **Increasing timeout values** (in graph files, lines with `timeout=`)
2. **Checking network connectivity** to Graph API and OpenRouter
3. **Checking LLM API status** on OpenRouter
4. **Scaling API infrastructure** if load is high

## Files Validated

- ✅ `src/agent/email_ingestion_graph.py` - Syntax valid, imports successfully
- ✅ `src/agent/email_classification_graph.py` - Syntax valid, imports successfully
- ✅ Both graphs load in langgraph dev without errors

## Next Steps

1. **Test**: Run langgraph dev and test email ingestion with sample data
2. **Monitor**: Watch logs for the first week
3. **Tune**: Adjust timeout values if needed based on your environment
4. **Document**: Maintain this changelog for future updates
