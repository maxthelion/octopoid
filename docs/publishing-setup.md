# Publishing Setup - Quick Start

One-time setup for npm package publishing via GitHub Actions.

## Steps (5 minutes)

### 1. Create npm Access Token

```bash
# Login to npm
npm login
# Enter username, password, email, OTP

# Create automation token
npm token create --read-only=false
```

**Copy the token** - it will only be shown once!

Example output:
```
┌────────────────┬──────────────────────────────────────┐
│ token          │ npm_xxxxxxxxxxxxxxxxxxxxxxxxxxxxx    │
├────────────────┼──────────────────────────────────────┤
│ created        │ 2026-02-11T13:30:00.000Z             │
├────────────────┼──────────────────────────────────────┤
│ readonly       │ false                                 │
├────────────────┼──────────────────────────────────────┤
│ automation     │ true                                  │
└────────────────┴──────────────────────────────────────┘
```

### 2. Add Token to GitHub

1. Go to: https://github.com/maxthelion/octopoid/settings/secrets/actions
2. Click **"New repository secret"**
3. Fill in:
   - **Name**: `NPM_TOKEN`
   - **Value**: Paste your npm token (starts with `npm_`)
4. Click **"Add secret"**

### 3. Verify Setup

Check that workflows exist:

```bash
cd /path/to/octopoid
ls -la .github/workflows/
# Should see:
# - publish-client.yml  (publishes on tag push)
# - ci.yml             (runs tests on PR/push)
```

### 4. Test (Optional but Recommended)

Create a test tag to verify workflow:

```bash
# Create a test pre-release tag
git tag -a v2.0.0-test.1 -m "Test publishing workflow"
git push origin v2.0.0-test.1

# Watch the workflow at:
# https://github.com/maxthelion/octopoid/actions

# Clean up test tag after verification
git tag -d v2.0.0-test.1
git push origin :refs/tags/v2.0.0-test.1
npm unpublish octopoid@2.0.0-test.1  # If it published
```

## You're Done! 🎉

Now you can publish by simply pushing a version tag:

```bash
# When ready for a real release:
cd packages/client
npm version patch  # Updates package.json to 2.0.1
git push origin main

git tag -a v2.0.1 -m "Release v2.0.1"
git push origin v2.0.1

# GitHub Actions will automatically:
# ✅ Run tests
# ✅ Build packages
# ✅ Publish to npm
# ✅ Create GitHub release
```

## Troubleshooting

### "Secret not found"

- Verify secret name is exactly `NPM_TOKEN` (all caps)
- Check you added it to the correct repository
- GitHub Actions can take a few minutes to recognize new secrets

### "Permission denied" during publish

Your npm token may not have the right permissions:

```bash
# Check your npm account has access
npm owner ls octopoid

# Add yourself if needed
npm owner add YOUR_USERNAME octopoid
```

### "Package doesn't exist on npm"

First publish must be done manually:

```bash
cd packages/client
npm publish --access public
```

Then future publishes will use GitHub Actions.

## Next Steps

- Read [docs/publishing.md](./publishing.md) for full publishing guide
- Review [CHANGELOG.md](../CHANGELOG.md) format
- Set up branch protection rules (optional but recommended)

## Security Notes

- ✅ Never commit npm tokens to git
- ✅ Never share npm tokens in Slack/email
- ✅ Rotate tokens every 90 days (best practice)
- ✅ Use automation tokens (not classic tokens) for CI/CD
- ✅ Limit token scope to only packages you need

## Help

If you have issues:

1. Check workflow logs: https://github.com/maxthelion/octopoid/actions
2. Verify npm token: `npm whoami`
3. Check package ownership: `npm owner ls octopoid`
4. Review [docs/publishing.md](./publishing.md)
