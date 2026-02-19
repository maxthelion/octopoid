#!/usr/bin/env bash
# Mock agent for scheduler lifecycle tests.
#
# Controlled entirely by MOCK_* environment variables — no Claude API calls.
#
# MOCK_OUTCOME:   success|failure|needs_continuation  (default: success)
# MOCK_DECISION:  approve|reject|empty                (default: empty — omit from result)
# MOCK_COMMENT:   text to include as "comment" field  (default: "")
# MOCK_REASON:    failure reason text                  (default: "Mock failure")
# MOCK_COMMITS:   number of git commits to make        (default: 0)
# MOCK_CRASH:     if "true", exit 1 without result.json (default: false)
# MOCK_SLEEP:     seconds to sleep before running      (default: 0)
# RESULT_FILE:    path for result.json                 (default: ../result.json)

set -uo pipefail

MOCK_OUTCOME="${MOCK_OUTCOME:-success}"
MOCK_DECISION="${MOCK_DECISION:-}"
MOCK_COMMENT="${MOCK_COMMENT:-}"
MOCK_REASON="${MOCK_REASON:-Mock failure}"
MOCK_COMMITS="${MOCK_COMMITS:-0}"
MOCK_CRASH="${MOCK_CRASH:-false}"
MOCK_SLEEP="${MOCK_SLEEP:-0}"
RESULT_FILE="${RESULT_FILE:-../result.json}"

# Sleep if requested
if [ "${MOCK_SLEEP}" != "0" ]; then
    sleep "${MOCK_SLEEP}"
fi

# Make git commits if requested
if [ "${MOCK_COMMITS}" -gt 0 ] 2>/dev/null; then
    git config user.email "mock-agent@test.local" 2>/dev/null || true
    git config user.name "Mock Agent" 2>/dev/null || true
    for i in $(seq 1 "${MOCK_COMMITS}"); do
        echo "Mock change ${i} at $(date)" >> mock-agent-output.txt
        git add mock-agent-output.txt
        git commit -m "mock: change ${i}"
    done
fi

# Crash without writing result.json
if [ "${MOCK_CRASH}" = "true" ]; then
    echo "Mock agent: crashing as requested" >&2
    exit 1
fi

# Build result.json
# json_str VARIABLE — safely JSON-encode a shell variable's value
json_str() {
    printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || printf '"%s"' "$1"
}

case "${MOCK_OUTCOME}" in
    success|done)
        DECISION_PART=""
        if [ -n "${MOCK_DECISION}" ] && [ "${MOCK_DECISION}" != "empty" ]; then
            DECISION_PART=", \"decision\": \"${MOCK_DECISION}\""
        fi

        COMMENT_PART=""
        if [ -n "${MOCK_COMMENT}" ]; then
            COMMENT_JSON=$(json_str "${MOCK_COMMENT}")
            COMMENT_PART=", \"comment\": ${COMMENT_JSON}"
        fi

        printf '{"outcome": "done", "status": "success"%s%s}\n' \
            "${DECISION_PART}" "${COMMENT_PART}" > "${RESULT_FILE}"
        ;;

    failure|failed)
        REASON_JSON=$(json_str "${MOCK_REASON}")
        printf '{"outcome": "failed", "status": "failure", "reason": %s}\n' \
            "${REASON_JSON}" > "${RESULT_FILE}"
        ;;

    needs_continuation)
        printf '{"outcome": "needs_continuation"}\n' > "${RESULT_FILE}"
        ;;

    *)
        echo "mock-agent: unknown MOCK_OUTCOME: ${MOCK_OUTCOME}" >&2
        exit 1
        ;;
esac

echo "Mock agent done. Result: $(cat "${RESULT_FILE}")"
