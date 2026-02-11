/**
 * Git operations for worktrees, branches, and pull requests
 */

import { execSync } from 'node:child_process'
import { existsSync, rmSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'
import simpleGit, { SimpleGit, SimpleGitOptions } from 'simple-git'
import { loadConfig, getRuntimeDir } from './config'

/**
 * Git command options
 */
const gitOptions: Partial<SimpleGitOptions> = {
  binary: 'git',
  maxConcurrentProcesses: 6,
  trimmed: false,
  timeout: {
    block: 120000, // 2 minutes
  },
}

/**
 * Get git instance for a directory
 */
function getGit(cwd?: string): SimpleGit {
  return simpleGit({
    ...gitOptions,
    baseDir: cwd || process.cwd(),
  })
}

/**
 * Run git command directly (for operations simple-git doesn't support well)
 */
function runGitCommand(
  args: string[],
  cwd?: string,
  options: { check?: boolean; timeout?: number } = {}
): { stdout: string; stderr: string; exitCode: number } {
  const { check = true, timeout = 120000 } = options

  try {
    const stdout = execSync(`git ${args.join(' ')}`, {
      cwd: cwd || process.cwd(),
      encoding: 'utf-8',
      timeout,
      stdio: ['pipe', 'pipe', 'pipe'],
    })

    return { stdout, stderr: '', exitCode: 0 }
  } catch (error: any) {
    if (check) {
      throw error
    }
    return {
      stdout: error.stdout?.toString() || '',
      stderr: error.stderr?.toString() || '',
      exitCode: error.status || 1,
    }
  }
}

/**
 * Get repository root directory
 */
export function getRepoRoot(): string {
  const config = loadConfig()
  return config.repo?.path || process.cwd()
}

/**
 * Get worktree path for an agent
 */
export function getWorktreePath(agentName: string): string {
  const runtimeDir = getRuntimeDir()
  return join(runtimeDir, '..', 'worktrees', agentName)
}

/**
 * Ensure a git worktree exists for an agent
 */
export async function ensureWorktree(
  agentName: string,
  baseBranch: string = 'main'
): Promise<string> {
  const repoRoot = getRepoRoot()
  const worktreePath = getWorktreePath(agentName)
  const git = getGit(repoRoot)

  // Check if worktree already exists and is valid
  if (existsSync(worktreePath) && existsSync(join(worktreePath, '.git'))) {
    const worktreeGit = getGit(worktreePath)

    try {
      // Update existing worktree to latest origin/main
      await worktreeGit.fetch('origin')
      // Reset to origin/main so worktree isn't based on stale local main
      await worktreeGit.raw(['checkout', '--detach', `origin/${baseBranch}`])
    } catch (error) {
      // Fetch may fail if offline, that's ok
      console.warn('Failed to update worktree, continuing with existing:', error)
    }

    return worktreePath
  }

  // Directory exists but is not a valid worktree - remove it
  if (existsSync(worktreePath)) {
    try {
      // Try to remove from git worktree list
      await git.raw(['worktree', 'remove', '--force', worktreePath])
    } catch {
      // May fail if not registered, that's ok
    }

    // Remove directory
    if (existsSync(worktreePath)) {
      rmSync(worktreePath, { recursive: true, force: true })
    }
  }

  // Create parent directory
  mkdirSync(join(worktreePath, '..'), { recursive: true })

  // Fetch latest from origin first
  try {
    await git.fetch('origin')
  } catch {
    // May fail if offline
  }

  // Create the worktree from origin/main (not local main)
  try {
    await git.raw([
      'worktree',
      'add',
      '--detach',
      worktreePath,
      `origin/${baseBranch}`,
    ])
  } catch (error: any) {
    // Fallback to local branch if origin ref doesn't exist
    if (
      error.message?.includes('invalid reference') ||
      error.message?.includes('not a valid')
    ) {
      await git.raw(['worktree', 'add', '--detach', worktreePath, baseBranch])
    } else {
      throw error
    }
  }

  return worktreePath
}

/**
 * Remove a git worktree for an agent
 */
export async function removeWorktree(agentName: string): Promise<void> {
  const repoRoot = getRepoRoot()
  const worktreePath = getWorktreePath(agentName)
  const git = getGit(repoRoot)

  if (existsSync(worktreePath)) {
    try {
      await git.raw(['worktree', 'remove', '--force', worktreePath])
    } catch (error) {
      console.error('Failed to remove worktree:', error)
    }
  }
}

/**
 * Create a feature branch for a task
 */
export async function createFeatureBranch(
  worktreePath: string,
  taskId: string,
  baseBranch: string = 'main'
): Promise<string> {
  const git = getGit(worktreePath)
  const timestamp = new Date()
    .toISOString()
    .replace(/[:.]/g, '-')
    .slice(0, 19)
    .replace('T', '-')
  const branchName = `agent/${taskId}-${timestamp}`

  // Fetch latest from origin
  try {
    await git.fetch('origin')
  } catch {
    // May fail if offline
  }

  // Checkout origin/main (detached)
  try {
    await git.raw(['checkout', '--detach', `origin/${baseBranch}`])
  } catch {
    // Fallback to local branch
    await git.checkout(baseBranch)
  }

  // Create and checkout new branch from detached HEAD
  await git.checkoutLocalBranch(branchName)

  return branchName
}

/**
 * Get current branch name
 */
export async function getCurrentBranch(worktreePath: string): Promise<string> {
  const git = getGit(worktreePath)
  const branch = await git.revparse(['--abbrev-ref', 'HEAD'])
  return branch.trim()
}

/**
 * Extract task ID from branch name
 * Pattern: agent/{task_id}-{timestamp}
 */
export function extractTaskIdFromBranch(branchName: string): string | null {
  if (!branchName.startsWith('agent/')) {
    return null
  }

  // Remove 'agent/' prefix
  const suffix = branchName.substring(6)

  // Match pattern: {task_id}-YYYYMMDD-HHMMSS or {task_id}-YYYY-MM-DD-HH-MM-SS
  const match = suffix.match(/^(.+?)-\d{4}-?\d{2}-?\d{2}/)
  if (match) {
    return match[1]
  }

  // Fallback: take everything before first dash-digit sequence
  const parts = suffix.split('-')
  if (parts.length > 0) {
    return parts[0]
  }

  return null
}

/**
 * Check if current branch has commits ahead of base
 */
export async function hasCommitsAheadOfBase(
  worktreePath: string,
  baseBranch: string = 'main'
): Promise<boolean> {
  try {
    const result = runGitCommand(
      ['rev-list', '--count', `${baseBranch}..HEAD`],
      worktreePath,
      { check: false }
    )

    if (result.exitCode !== 0) {
      return false
    }

    const count = parseInt(result.stdout.trim(), 10)
    return count > 0
  } catch {
    return false
  }
}

/**
 * Check if worktree has uncommitted changes
 */
export async function hasUncommittedChanges(
  worktreePath: string
): Promise<boolean> {
  const git = getGit(worktreePath)
  const status = await git.status()
  return status.files.length > 0
}

/**
 * Commit all changes in worktree
 */
export async function commitChanges(
  worktreePath: string,
  message: string
): Promise<boolean> {
  const git = getGit(worktreePath)

  if (!(await hasUncommittedChanges(worktreePath))) {
    return false
  }

  await git.add('.')
  await git.commit(message)
  return true
}

/**
 * Push branch to origin
 */
export async function pushBranch(
  worktreePath: string,
  branchName: string
): Promise<void> {
  const git = getGit(worktreePath)
  await git.push('origin', branchName, ['--set-upstream'])
}

/**
 * Create pull request using gh CLI
 */
export async function createPullRequest(
  worktreePath: string,
  branchName: string,
  baseBranch: string,
  title: string,
  body: string
): Promise<string> {
  // Push branch first
  await pushBranch(worktreePath, branchName)

  // Check if PR already exists
  try {
    const result = execSync(
      `gh pr view ${branchName} --json url -q .url`,
      {
        cwd: worktreePath,
        encoding: 'utf-8',
        stdio: ['pipe', 'pipe', 'pipe'],
        timeout: 30000,
      }
    )

    const url = result.trim()
    if (url) {
      return url
    }
  } catch {
    // PR doesn't exist yet, continue to create
  }

  // Create new PR
  const result = execSync(
    `gh pr create --base ${baseBranch} --head ${branchName} --title "${title.replace(/"/g, '\\"')}" --body "${body.replace(/"/g, '\\"')}"`,
    {
      cwd: worktreePath,
      encoding: 'utf-8',
      timeout: 60000,
    }
  )

  return result.trim()
}

/**
 * Count open pull requests
 */
export async function countOpenPRs(label?: string): Promise<number> {
  try {
    let cmd = 'gh pr list --state open --json number'
    if (label) {
      cmd += ` --label ${label}`
    }

    const result = execSync(cmd, {
      encoding: 'utf-8',
      timeout: 30000,
    })

    const prs = JSON.parse(result)
    return Array.isArray(prs) ? prs.length : 0
  } catch {
    return 0
  }
}

/**
 * List open pull requests
 */
export interface PullRequest {
  number: number
  title: string
  url: string
  headRefName: string
  author: {
    login: string
  }
}

export async function listOpenPRs(author?: string): Promise<PullRequest[]> {
  try {
    let cmd =
      'gh pr list --state open --json number,title,url,headRefName,author'
    if (author) {
      cmd += ` --author ${author}`
    }

    const result = execSync(cmd, {
      encoding: 'utf-8',
      timeout: 30000,
    })

    return JSON.parse(result)
  } catch {
    return []
  }
}

/**
 * Get git remote URL
 */
export async function getRemoteUrl(
  worktreePath: string,
  remoteName: string = 'origin'
): Promise<string | null> {
  const git = getGit(worktreePath)

  try {
    const remotes = await git.getRemotes(true)
    const remote = remotes.find((r) => r.name === remoteName)
    return remote?.refs?.fetch || null
  } catch {
    return null
  }
}

/**
 * Check if repository is clean (no uncommitted changes)
 */
export async function isClean(worktreePath: string): Promise<boolean> {
  return !(await hasUncommittedChanges(worktreePath))
}

/**
 * Get list of changed files
 */
export async function getChangedFiles(worktreePath: string): Promise<string[]> {
  const git = getGit(worktreePath)
  const status = await git.status()
  return status.files.map((f) => f.path)
}

/**
 * Get commit count on current branch
 */
export async function getCommitCount(
  worktreePath: string,
  baseBranch: string = 'main'
): Promise<number> {
  try {
    const result = runGitCommand(
      ['rev-list', '--count', `${baseBranch}..HEAD`],
      worktreePath,
      { check: false }
    )

    if (result.exitCode !== 0) {
      return 0
    }

    return parseInt(result.stdout.trim(), 10) || 0
  } catch {
    return 0
  }
}

/**
 * Get commit messages on current branch
 */
export async function getCommitMessages(
  worktreePath: string,
  baseBranch: string = 'main'
): Promise<string[]> {
  const git = getGit(worktreePath)

  try {
    const log = await git.log({
      from: baseBranch,
      to: 'HEAD',
    })

    return log.all.map((commit) => commit.message)
  } catch {
    return []
  }
}

/**
 * Fetch from origin
 */
export async function fetch(
  worktreePath: string,
  remote: string = 'origin'
): Promise<void> {
  const git = getGit(worktreePath)
  await git.fetch(remote)
}

/**
 * Pull changes from remote
 */
export async function pull(
  worktreePath: string,
  remote: string = 'origin',
  branch?: string
): Promise<void> {
  const git = getGit(worktreePath)
  if (branch) {
    await git.pull(remote, branch)
  } else {
    await git.pull()
  }
}

/**
 * Check if branch exists locally
 */
export async function branchExists(
  worktreePath: string,
  branchName: string
): Promise<boolean> {
  const git = getGit(worktreePath)

  try {
    const branches = await git.branchLocal()
    return branches.all.includes(branchName)
  } catch {
    return false
  }
}

/**
 * Delete local branch
 */
export async function deleteBranch(
  worktreePath: string,
  branchName: string,
  force: boolean = false
): Promise<void> {
  const git = getGit(worktreePath)
  await git.deleteLocalBranch(branchName, force)
}

/**
 * Checkout branch
 */
export async function checkoutBranch(
  worktreePath: string,
  branchName: string
): Promise<void> {
  const git = getGit(worktreePath)
  await git.checkout(branchName)
}

/**
 * Get diff between branches or commits
 */
export async function getDiff(
  worktreePath: string,
  from?: string,
  to?: string
): Promise<string> {
  const git = getGit(worktreePath)

  if (from && to) {
    return await git.diff([`${from}..${to}`])
  } else if (from) {
    return await git.diff([from])
  } else {
    return await git.diff()
  }
}

/**
 * Stash changes
 */
export async function stash(
  worktreePath: string,
  message?: string
): Promise<void> {
  const git = getGit(worktreePath)
  if (message) {
    await git.stash(['save', message])
  } else {
    await git.stash()
  }
}

/**
 * Pop stashed changes
 */
export async function stashPop(worktreePath: string): Promise<void> {
  const git = getGit(worktreePath)
  await git.stash(['pop'])
}
