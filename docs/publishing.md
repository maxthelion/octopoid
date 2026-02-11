# Publishing Workflow

Guide for publishing Octopoid packages to npm.

## Overview

Octopoid uses GitHub Actions for automated publishing. When you push a version tag, the workflow automatically:

1. ✅ Runs type checks on all packages
2. ✅ Builds all packages
3. ✅ Runs unit tests
4. ✅ Publishes to npm
5. ✅ Creates a GitHub release

## Prerequisites

### One-Time Setup

1. **Get npm Access Token**

```bash
# Login to npm
npm login

# Create automation token (recommended) or classic token
npm token create --read-only=false
```

Copy the token - you'll need it for the next step.

2. **Add Token to GitHub Secrets**

- Go to: https://github.com/maxthelion/octopoid/settings/secrets/actions
- Click "New repository secret"
- Name: `NPM_TOKEN`
- Value: Paste your npm token
- Click "Add secret"

3. **Verify Workflows Are Enabled**

- Go to: https://github.com/maxthelion/octopoid/actions
- Ensure Actions are enabled for the repository

## Publishing a New Version

### 1. Prepare the Release

```bash
# Ensure you're on main and up to date
git checkout main
git pull origin main

# Update version in package.json
cd packages/client
npm version patch  # or minor, or major
# This updates version in package.json and creates a git commit

# Update CHANGELOG.md with release notes
vim ../../CHANGELOG.md

# Commit changelog
git add ../../CHANGELOG.md
git commit -m "docs: update changelog for v2.0.1"
```

### 2. Create and Push Tag

```bash
# Create annotated tag
git tag -a v2.0.1 -m "Release v2.0.1

- Fix ESM import issues
- Add integration tests
- Improve error handling
"

# Push tag to trigger publish workflow
git push origin v2.0.1
```

### 3. Monitor the Workflow

- Go to: https://github.com/maxthelion/octopoid/actions
- Watch the "Publish Client" workflow run
- Verify all steps pass:
  - ✅ Type check
  - ✅ Build
  - ✅ Test
  - ✅ Publish @octopoid/shared
  - ✅ Publish octopoid (client)
  - ✅ Create GitHub Release

### 4. Verify Publication

```bash
# Check npm
npm view octopoid version
# Should show: 2.0.1

# Test installation
npm install -g octopoid@2.0.1
octopoid --version
```

## Version Numbering

Follow [Semantic Versioning](https://semver.org/):

- **Patch** (2.0.0 → 2.0.1): Bug fixes, no breaking changes
  ```bash
  npm version patch
  ```

- **Minor** (2.0.0 → 2.1.0): New features, no breaking changes
  ```bash
  npm version minor
  ```

- **Major** (2.0.0 → 3.0.0): Breaking changes
  ```bash
  npm version major
  ```

## Rollback a Bad Release

If you published a broken version:

### Option 1: Publish a Patch (Recommended)

```bash
# Fix the issue
# ...

# Publish new patch version
npm version patch
git push origin main
git tag -a v2.0.2 -m "Fix: resolve issue from v2.0.1"
git push origin v2.0.2
```

### Option 2: Deprecate (Don't Delete)

```bash
# Deprecate the bad version (users get a warning)
npm deprecate octopoid@2.0.1 "This version has issues, please upgrade to 2.0.2"
```

**Never use `npm unpublish`** - it breaks existing installations and violates npm policies.

## Pre-Release Versions

For testing before a full release:

```bash
# Create pre-release version
cd packages/client
npm version prerelease --preid=beta
# Creates: 2.0.1-beta.0

# Push tag
git push origin v2.0.1-beta.0

# Users can install with:
npm install -g octopoid@beta
```

## Manual Publishing (Emergency)

If GitHub Actions is down:

```bash
# Ensure everything is built and tested
cd packages/shared && pnpm build && pnpm test
cd ../client && pnpm build && pnpm test

# Login to npm
npm login

# Publish shared package
cd packages/shared
npm publish --access public

# Publish client package
cd ../client
npm publish --access public
```

## Troubleshooting

### "Package already published"

You're trying to publish a version that already exists on npm. Increment the version number.

### "You do not have permission to publish"

- Verify you're logged in: `npm whoami`
- Check you're a maintainer: `npm owner ls octopoid`
- Verify NPM_TOKEN is correct in GitHub Secrets

### "Tests failed"

The workflow won't publish if tests fail. Fix the tests first:

```bash
# Run tests locally
cd packages/client
pnpm test

# Fix issues, commit, and re-push the tag
git tag -d v2.0.1  # Delete local tag
git push origin :refs/tags/v2.0.1  # Delete remote tag
# Fix issues, commit
git tag -a v2.0.1 -m "..."
git push origin v2.0.1
```

### "Workflow didn't trigger"

- Ensure tag starts with `v` (e.g., `v2.0.1`, not `2.0.1`)
- Check Actions are enabled in repository settings
- Verify workflow file exists at `.github/workflows/publish-client.yml`

## Workflow Configuration

The publish workflow is defined in `.github/workflows/publish-client.yml`:

- **Trigger**: Git tags matching `v*.*.*` pattern
- **Secrets Required**: `NPM_TOKEN`, `GITHUB_TOKEN` (auto-provided)
- **Publishes**: `@octopoid/shared`, `octopoid` (client)
- **Creates**: GitHub Release with install instructions

## Related Workflows

- **CI** (`.github/workflows/ci.yml`): Runs on every push/PR
  - Type checking
  - Unit tests
  - Integration tests
  - Build verification

- **Deploy Server** (`.github/workflows/deploy-server.yml`): Deploys server to Cloudflare
  - Separate from client publishing
  - Manually triggered or on main branch push

## Best Practices

1. **Always test locally first**
   ```bash
   pnpm test
   pnpm build
   ```

2. **Update CHANGELOG.md** before tagging

3. **Use annotated tags** with descriptive messages
   ```bash
   git tag -a v2.0.1 -m "Detailed release notes"
   ```

4. **Don't rush** - once published, you can't unpublish

5. **Monitor npm** for the first few hours after release
   - Check download stats
   - Watch for GitHub issues from users

6. **Test installation** after publishing
   ```bash
   npm install -g octopoid@latest
   ```

## Resources

- [npm Publishing Guide](https://docs.npmjs.com/packages-and-modules/contributing-packages-to-the-registry)
- [Semantic Versioning](https://semver.org/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github)
