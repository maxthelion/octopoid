# refactor-09: Add agent directory scaffolding to octopoid init

ROLE: implement
PRIORITY: P2
BRANCH: feature/client-server-architecture
CREATED: 2026-02-15T00:00:00Z
CREATED_BY: human
SKIP_PR: true
DEPENDS_ON: refactor-07, refactor-08

## Context

When a user runs `octopoid init`, it creates the `.octopoid/` directory structure and copies template files. Currently it copies `templates/agents.yaml` into `.octopoid/agents.yaml` (see `packages/client/src/commands/init.ts`, lines 92-98).

With agent directories (refactor-07, refactor-08), init should also copy the agent type templates from `packages/client/agents/` into `.octopoid/agents/` in the user's project. After scaffolding, the user owns these files and can customise them.

Reference: `project-management/drafts/9-2026-02-15-agent-directories.md` (Scaffolding section)

## What to do

### Update `packages/client/src/commands/init.ts`

Add a step after the existing agents.yaml copy (around line 98) that scaffolds agent directories.

#### Logic

1. Define the source path: `packages/client/agents/` (relative to the package root, same pattern as `templates/agents.yaml`)
2. Define the destination: `.octopoid/agents/` in the user's project
3. For each subdirectory in the source (e.g., `implementer/`, `gatekeeper/`):
   - If the destination directory already exists, skip it (don't overwrite user customisations)
   - Otherwise, recursively copy the entire directory

#### Implementation approach

The init command is TypeScript. Use Node.js built-in fs functions:

```typescript
import { cpSync, readdirSync } from 'node:fs'

// After the agents.yaml copy...

// Scaffold agent directories
const agentsTemplateDir = join(__dirname, '..', '..', 'agents')
const agentsDestDir = join(octopoidDir, 'agents')

if (existsSync(agentsTemplateDir)) {
  mkdirSync(agentsDestDir, { recursive: true })

  for (const entry of readdirSync(agentsTemplateDir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue

    const destPath = join(agentsDestDir, entry.name)
    if (existsSync(destPath)) {
      console.log(`  Skipping ${entry.name}/ (already exists)`)
      continue
    }

    cpSync(join(agentsTemplateDir, entry.name), destPath, { recursive: true })
    console.log(`  Created: .octopoid/agents/${entry.name}/`)
  }
}
```

Note: `cpSync` requires Node.js 16.7+. Check the project's minimum Node version. If older, use a recursive copy utility or `fs-extra`.

#### Update console output

Add a line to the "Next steps" section mentioning agent customisation:

```
  4. Customise agents: .octopoid/agents/
```

### Verify `packages/client/agents/` is included in the npm package

Check `packages/client/package.json` to ensure the `agents/` directory is included when the package is published. If there's a `files` field in package.json, add `"agents"` to it. If the package uses a build step that copies files to `dist/`, ensure agents are copied too.

### Test manually

After making changes:

1. Build the package (if needed): `npm run build` in `packages/client/`
2. Create a temp directory
3. Run `octopoid init --server http://localhost:8787` from the temp directory
4. Verify `.octopoid/agents/implementer/` and `.octopoid/agents/gatekeeper/` are created
5. Run init again -- verify existing directories are NOT overwritten
6. Clean up temp directory

## Key files

- `packages/client/src/commands/init.ts` -- update init logic here (lines 92-98 area)
- `packages/client/agents/` -- template source (from refactor-07, refactor-08)
- `packages/client/package.json` -- ensure agents/ is included in package
- `project-management/drafts/9-2026-02-15-agent-directories.md` -- design reference

## Acceptance criteria

- [ ] `octopoid init` copies agent template directories to `.octopoid/agents/`
- [ ] Each agent type directory (implementer/, gatekeeper/) is copied with all files and subdirectories
- [ ] Existing directories are NOT overwritten (skip with message)
- [ ] Console output shows which directories were created/skipped
- [ ] Works with the existing init flow (config.yaml, agents.yaml, .gitignore still created)
- [ ] `packages/client/agents/` is included in the package distribution
- [ ] Fresh init produces correct directory structure in `.octopoid/agents/`
- [ ] All existing tests pass
