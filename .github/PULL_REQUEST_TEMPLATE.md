## Summary

<!-- Brief description of what this PR does -->

## Deployment Checklist

### Fix Identification
- **Root cause**: <!-- What caused the issue? -->
- **Fix commit SHA**: <!-- e.g. abc1234 -->

### Merge Status
- [ ] PR merged into `master`

### Deployment Targets
<!-- Check all that apply -->
- [ ] Frontend (Azure Static Web Apps)
- [ ] Backend API (aitherhubAPI)
- [ ] Backend API (fast-api-kyogoku)
- [ ] Worker VM (requires manual `git pull` via `az vm run-command`)

### Production Verification
- [ ] `/version` endpoint shows expected commit SHA
- [ ] `/health/ready` returns 200
- [ ] Target page/feature works as expected

### Verification Details
- **Production commit SHA**: <!-- Check /version endpoint after deploy -->
- **Verification result**: <!-- What did you see on the target page? -->
- **Remaining risk**: <!-- Any known issues or follow-up needed? -->

## How to Verify in Production

<!-- 
Describe the exact steps to confirm this change is live:
1. Go to https://www.aitherhub.com/...
2. Click on ...
3. Expected: ...
-->
