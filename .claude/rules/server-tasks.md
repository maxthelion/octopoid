When creating tasks for octopoid-server (the server submodule), write them as markdown files to `project-management/tasks/octopoid-server/` instead of using `create_task()`. The server is a separate project and can't be enqueued on the octopoid orchestrator queue.

The local submodule checkout (`submodules/server/`) is almost always behind the deployed version. **Never use the local checkout to determine what the server supports.** Instead:
- **Test the live API** via the SDK (`get_sdk()`) or `curl` against the production URL
- **Check the remote repo** with `(cd submodules/server && git log origin/main --oneline)` or `git show origin/main:path/to/file`
- The deployed server is the source of truth for available endpoints, schema, and features
