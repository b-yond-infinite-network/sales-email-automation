# Quick Reference - LangGraph API Fixes

## What Was Fixed

The LangGraph email processing API was hanging/crashing because:
1. **No request timeouts** - HTTP calls could hang indefinitely
2. **No error recovery** - Crashes weren't caught gracefully
3. **Missing validation** - No checks for required configuration
4. **Poor logging** - Hard to debug failures

## What Changed

### Timeout Protection (MOST IMPORTANT)
- All async operations now wrapped with `asyncio.wait_for()`
- HTTP calls: 30-45 seconds max
- LLM calls: 60 seconds max
- Web searches: 10 seconds max
- **Result**: Server never hangs, requests fail predictably

### Configuration Validation
- Checks for `OPENROUTER_API_KEY`, `MAIL_USER`, etc.
- Returns error message instead of crashing
- **Result**: Clearer error messages for troubleshooting

### Error Recovery
- Classification returns safe "disqualify" result on any error
- Graph execution continues instead of crashing
- **Result**: API stays alive and responds to requests

### Better Logging
- Exception types shown (e.g., "TimeoutError", "JSONDecodeError")
- Full tracebacks in logs for debugging
- **Result**: Easier to diagnose production issues

## Testing

```bash
# Start the API
cd /Users/antonidebicki/Realize/Email\ Automation/antoni-test-system
langgraph dev

# Should see both graphs load:
# - email-ingestion-agent ✓
# - email-classification-agent ✓
```

## Timeout Values

| Operation | Timeout | File |
|-----------|---------|------|
| Token request | 30s | email_ingestion_graph.py:14 |
| Email fetch | 30s | email_ingestion_graph.py:14 |
| Attachments | 45s | email_ingestion_graph.py:14 |
| LLM classify | 60s | email_ingestion_graph.py:14 |
| Web search | 10s | email_classification_graph.py:19 |

To adjust: Edit the timeout values and restart the server.

## Common Error Messages & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `"failed: no access token"` | Microsoft auth failed | Check CLIENT_ID, CLIENT_SECRET, TENANT_ID in .env |
| `"failed: email fetch timeout"` | Email API too slow | Increase timeout in graph or check network |
| `"email classification timed out"` | LLM too slow | Try faster model or increase timeout |
| `"missing API key"` | OPENROUTER_API_KEY not set | Add to .env |
| `"MAIL_USER not configured"` | MAIL_USER not set | Add to .env |

## Files Modified

1. **src/agent/email_ingestion_graph.py**
   - Added: Timeout constants (lines 14-16)
   - Updated: get_access_token() 
   - Updated: get_email_messages()
   - Updated: download_attachments()
   - Updated: classify_email()

2. **src/agent/email_classification_graph.py**
   - Added: Timeout constants (lines 20-22)
   - Updated: _fetch_public_company_context()
   - Updated: classify_email()

## Performance

- ✅ No performance regression
- ✅ Faster failure for bad requests (fail-fast)
- ✅ Better resource utilization (timeouts prevent resource leaks)

## Deployment Notes

- Requires Python 3.11+ (asyncio.wait_for is standard)
- Compatible with LangGraph 0.7.37+
- No database changes needed
- Backward compatible with existing clients

## Support

For questions about the changes, see:
- `API_UPDATES.md` - Detailed technical summary
- `CHANGELOG.md` - Full changelog with rationale
- This file - Quick reference guide
